from dataclasses import dataclass
import hashlib
import logging
import os
from pathlib import Path
import stat
from threading import Barrier, Thread

import pytest

from apps.agent.src.agent_orchestration.plugins.skill.repository import (
    RepositoryBatchResult,
    SkillRepository,
)


@dataclass(frozen=True)
class Operation:
    action: str
    target_name: str | None = None
    source_names: tuple[str, ...] = ()
    content: str | None = None


def skill_content(
    name: str,
    description: str = "Use when testing repository behavior.",
    body: str = "# Instructions\n\nDo the safe thing.\n",
) -> str:
    return (
        "---\n"
        f"name: {name}\n"
        f"description: {description}\n"
        "---\n"
        f"{body}"
    )


def write_skill(
    root: Path,
    name: str,
    *,
    content: str | None = None,
) -> Path:
    skill_dir = root / name
    skill_dir.mkdir(parents=True, mode=0o700)
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text(content or skill_content(name), encoding="utf-8")
    return skill_file


def repository(tmp_path: Path) -> SkillRepository:
    return SkillRepository(
        tmp_path / "global",
        tmp_path / "workspace",
    )


def test_repository_create写入workspace并收紧权限和生成快照(tmp_path):
    repo = repository(tmp_path)
    content = skill_content(
        "new-skill",
        description="Use when a new skill is needed.",
    )

    result = repo.create(" New-Skill ", content)

    skill_file = tmp_path / "workspace" / "new-skill" / "SKILL.md"
    assert result.status == "success"
    assert result.path == skill_file.resolve()
    assert skill_file.read_text(encoding="utf-8") == content
    assert (tmp_path / "workspace").stat().st_mode & 0o777 == 0o700
    assert skill_file.parent.stat().st_mode & 0o777 == 0o700
    assert skill_file.stat().st_mode & 0o777 == 0o600
    assert not list(skill_file.parent.glob(".SKILL.md.*.tmp"))

    snapshot = repo.snapshot()
    assert len(snapshot) == 1
    assert snapshot[0].name == "new-skill"
    assert snapshot[0].description == "Use when a new skill is needed."
    assert snapshot[0].scope == "workspace"
    assert snapshot[0].content == content
    assert snapshot[0].content_hash == hashlib.sha256(
        content.encode("utf-8")
    ).hexdigest()


@pytest.mark.parametrize(
    "unsafe_name",
    [
        "../escape",
        "/absolute",
        "nested/name",
        ".hidden",
        "snowman-☃",
        "a" * 65,
    ],
)
def test_repository_create拒绝不安全名称且不逃逸workspace(tmp_path, unsafe_name):
    repo = repository(tmp_path)

    result = repo.create(unsafe_name, skill_content("safe-name"))

    assert result.status == "failed"
    assert not (tmp_path / "escape").exists()
    assert not (tmp_path / "workspace").exists()


@pytest.mark.parametrize(
    "invalid_content",
    [
        "# no front matter\n",
        "---\nname: target\ndescription:\n---\n",
        skill_content("different-name"),
        "---\nname: [broken\ndescription: bad\n---\n",
        "---\nname: target\ndescription: ok\n---\n\ud800",
    ],
)
def test_repository_create拒绝不完整或非utf8_skill内容(tmp_path, invalid_content):
    repo = repository(tmp_path)

    result = repo.create("target", invalid_content)

    assert result.status == "failed"
    assert not (tmp_path / "workspace" / "target" / "SKILL.md").exists()


def test_repository_update全局skill只写workspace覆盖版本(tmp_path):
    global_file = write_skill(
        tmp_path / "global",
        "shared",
        content=skill_content("shared", body="global body\n"),
    )
    repo = repository(tmp_path)
    analysis = repo.snapshot()
    replacement = skill_content("shared", body="workspace body\n")

    result = repo.update("shared", replacement, analysis)

    workspace_file = tmp_path / "workspace" / "shared" / "SKILL.md"
    assert result.status == "success"
    assert global_file.read_text(encoding="utf-8") == skill_content(
        "shared",
        body="global body\n",
    )
    assert workspace_file.read_text(encoding="utf-8") == replacement
    visible = repo.snapshot()
    assert len(visible) == 1
    assert visible[0].scope == "workspace"
    assert visible[0].content == replacement


def test_repository_create和update在分析后变化时跳过(tmp_path):
    repo = repository(tmp_path)
    empty_analysis = repo.snapshot()
    appeared = write_skill(tmp_path / "workspace", "appeared")

    create_result = repo.create(
        "appeared",
        skill_content("appeared", body="replacement\n"),
        empty_analysis,
    )
    analysis = repo.snapshot()
    changed = skill_content("appeared", body="changed concurrently\n")
    appeared.write_text(changed, encoding="utf-8")
    update_result = repo.update(
        "appeared",
        skill_content("appeared", body="maintainer update\n"),
        analysis,
    )

    assert create_result.status == "skipped"
    assert update_result.status == "skipped"
    assert appeared.read_text(encoding="utf-8") == changed


def test_repository_merge先写目标再删除workspace来源并保留global来源(tmp_path):
    workspace_source = write_skill(tmp_path / "workspace", "workspace-a")
    global_source = write_skill(tmp_path / "global", "global-b")
    repo = repository(tmp_path)
    analysis = repo.snapshot(
        lifecycle_by_skill_key={
            "workspace:workspace-a": "deletion_candidate",
        }
    )
    merged_content = skill_content("merged", body="merged body\n")

    result = repo.merge(
        "merged",
        ("workspace-a", "global-b"),
        merged_content,
        analysis,
    )

    target = tmp_path / "workspace" / "merged" / "SKILL.md"
    assert result.status == "success"
    assert target.read_text(encoding="utf-8") == merged_content
    assert not workspace_source.exists()
    assert not workspace_source.parent.exists()
    assert global_source.exists()


def test_repository_merge活跃workspace来源只写目标不自动删除(tmp_path):
    active_a = write_skill(tmp_path / "workspace", "active-a")
    active_b = write_skill(tmp_path / "workspace", "active-b")
    repo = repository(tmp_path)
    analysis = repo.snapshot()

    result = repo.merge(
        "merged",
        ("active-a", "active-b"),
        skill_content("merged"),
        analysis,
    )

    assert result.status == "success"
    assert result.target_written is True
    assert result.deleted_sources == ()
    assert result.retained_sources == ("active-a", "active-b")
    assert active_a.exists()
    assert active_b.exists()


def test_repository_merge写入失败时不删除任何来源(tmp_path, monkeypatch):
    source_a = write_skill(tmp_path / "workspace", "source-a")
    source_b = write_skill(tmp_path / "workspace", "source-b")
    repo = repository(tmp_path)
    analysis = repo.snapshot()

    def fail_replace(source, target, **kwargs):
        raise OSError("simulated replace failure")

    monkeypatch.setattr(
        "apps.agent.src.agent_orchestration.plugins.skill.repository.os.replace",
        fail_replace,
    )

    result = repo.merge(
        "merged",
        ("source-a", "source-b"),
        skill_content("merged"),
        analysis,
    )

    assert result.status == "failed"
    assert source_a.exists()
    assert source_b.exists()
    target_dir = tmp_path / "workspace" / "merged"
    assert not (target_dir / "SKILL.md").exists()
    assert not list(target_dir.glob(".SKILL.md.*.tmp"))


def test_repository_merge来源hash冲突时不写目标也不删除来源(tmp_path):
    source_a = write_skill(tmp_path / "workspace", "source-a")
    source_b = write_skill(tmp_path / "workspace", "source-b")
    repo = repository(tmp_path)
    analysis = repo.snapshot()
    concurrent_content = skill_content(
        "source-b",
        body="concurrent update\n",
    )
    source_b.write_text(concurrent_content, encoding="utf-8")

    result = repo.merge(
        "merged",
        ("source-a", "source-b"),
        skill_content("merged"),
        analysis,
    )

    assert result.status == "skipped"
    assert source_a.exists()
    assert source_b.read_text(encoding="utf-8") == concurrent_content
    assert not (tmp_path / "workspace" / "merged").exists()


def test_repository_delete只允许workspace删除候选且只移除空目录(tmp_path):
    global_file = write_skill(tmp_path / "global", "global-old")
    workspace_file = write_skill(tmp_path / "workspace", "workspace-old")
    attachment = workspace_file.parent / "notes.txt"
    attachment.write_text("keep me", encoding="utf-8")
    repo = repository(tmp_path)
    active_analysis = repo.snapshot()

    active_result = repo.delete("workspace-old", active_analysis)
    candidate_analysis = repo.snapshot(
        lifecycle_by_skill_key={
            "global:global-old": "deletion_candidate",
            "workspace:workspace-old": "deletion_candidate",
        }
    )
    global_result = repo.delete("global-old", candidate_analysis)
    workspace_result = repo.delete("workspace-old", candidate_analysis)

    assert active_result.status == "failed"
    assert global_result.status == "failed"
    assert workspace_result.status == "success"
    assert global_file.exists()
    assert not workspace_file.exists()
    assert workspace_file.parent.is_dir()
    assert attachment.read_text(encoding="utf-8") == "keep me"


def test_repository_delete在分析后hash变化时跳过(tmp_path):
    skill_file = write_skill(tmp_path / "workspace", "stale")
    repo = repository(tmp_path)
    analysis = repo.snapshot(
        lifecycle_by_skill_key={
            "workspace:stale": "deletion_candidate",
        }
    )
    changed = skill_content("stale", body="new information\n")
    skill_file.write_text(changed, encoding="utf-8")

    result = repo.delete("stale", analysis)

    assert result.status == "skipped"
    assert skill_file.read_text(encoding="utf-8") == changed


def test_repository拒绝symlink写入和删除且不修改边界外文件(tmp_path):
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    outside_file = write_skill(outside_dir, "escaped")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "escaped").symlink_to(outside_file.parent, target_is_directory=True)
    repo = SkillRepository(tmp_path / "global", workspace)

    create_result = repo.create("escaped", skill_content("escaped"))

    assert create_result.status == "failed"
    assert outside_file.read_text(encoding="utf-8") == skill_content("escaped")


def test_repository拒绝重叠根目录和symlink_workspace根(tmp_path):
    global_dir = tmp_path / "skills"
    with pytest.raises(ValueError, match="must not overlap"):
        SkillRepository(global_dir, global_dir / "workspace")

    real_workspace = tmp_path / "real-workspace"
    real_workspace.mkdir()
    linked_workspace = tmp_path / "linked-workspace"
    linked_workspace.symlink_to(real_workspace, target_is_directory=True)
    repo = SkillRepository(tmp_path / "global", linked_workspace)

    result = repo.create("safe", skill_content("safe"))

    assert result.status == "failed"
    assert not (real_workspace / "safe").exists()


def test_repository_apply返回结构化结果且隔离单项异常(tmp_path):
    repo = repository(tmp_path)
    operations = [
        Operation(action="unsupported", target_name="bad"),
        Operation(
            action="create",
            target_name="created",
            content=skill_content("created"),
        ),
        Operation(action="no_op"),
    ]

    result = repo.apply(operations, analysis_snapshots=())

    assert isinstance(result, RepositoryBatchResult)
    assert [item.status for item in result.results] == [
        "failed",
        "success",
        "skipped",
    ]
    assert result.success_count == 1
    assert result.failed_count == 1
    assert result.skipped_count == 1
    assert result.ok is False
    assert (
        tmp_path / "workspace" / "created" / "SKILL.md"
    ).read_text(encoding="utf-8") == skill_content("created")


def test_repository原子写使用同目录replace并fsync文件和父目录(tmp_path, monkeypatch):
    repo = repository(tmp_path)
    real_replace = os.replace
    real_fsync = os.fsync
    replace_calls: list[tuple[Path, Path, int, int]] = []
    fsync_calls: list[os.stat_result] = []

    def recording_replace(source, target, *, src_dir_fd=None, dst_dir_fd=None):
        replace_calls.append(
            (Path(source), Path(target), src_dir_fd, dst_dir_fd)
        )
        real_replace(
            source,
            target,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
        )

    def recording_fsync(descriptor):
        fsync_calls.append(os.fstat(descriptor))
        real_fsync(descriptor)

    monkeypatch.setattr(
        "apps.agent.src.agent_orchestration.plugins.skill.repository.os.replace",
        recording_replace,
    )
    monkeypatch.setattr(
        "apps.agent.src.agent_orchestration.plugins.skill.repository.os.fsync",
        recording_fsync,
    )

    result = repo.create("atomic", skill_content("atomic"))

    assert result.status == "success"
    assert len(replace_calls) == 1
    temporary, target, source_fd, target_fd = replace_calls[0]
    assert source_fd == target_fd
    assert temporary.parent == target.parent
    assert target.name == "SKILL.md"
    fsynced_directories = {
        (item.st_dev, item.st_ino)
        for item in fsync_calls
        if stat.S_ISDIR(item.st_mode)
    }
    for directory in (
        tmp_path,
        tmp_path / "workspace",
        tmp_path / "workspace" / "atomic",
    ):
        directory_status = directory.stat()
        assert (
            directory_status.st_dev,
            directory_status.st_ino,
        ) in fsynced_directories


def test_repository扫描global根普通sqlite文件不warning(tmp_path, caplog):
    global_dir = tmp_path / "global"
    global_dir.mkdir()
    (global_dir / "skill-state.sqlite3").write_bytes(b"sqlite")
    write_skill(global_dir, "valid-skill")
    repo = repository(tmp_path)

    with caplog.at_level(logging.WARNING):
        snapshots = repo.snapshot()

    assert [snapshot.name for snapshot in snapshots] == ["valid-skill"]
    assert "skill-state.sqlite3" not in caplog.text


def test_repository_replace前目录交换不提交也不修改workspace外文件(
    tmp_path,
    monkeypatch,
):
    workspace_file = write_skill(tmp_path / "workspace", "victim")
    original_content = workspace_file.read_text(encoding="utf-8")
    outside_file = write_skill(tmp_path / "outside", "victim")
    repo = repository(tmp_path)
    analysis = repo.snapshot()
    module = __import__(
        "apps.agent.src.agent_orchestration.plugins.skill.repository",
        fromlist=["_create_temporary_file"],
    )
    real_create_temporary_file = module._create_temporary_file

    def create_temporary_then_exchange(skill_fd):
        result = real_create_temporary_file(skill_fd)
        detached = workspace_file.parent.with_name("victim-detached")
        workspace_file.parent.rename(detached)
        workspace_file.parent.symlink_to(
            outside_file.parent,
            target_is_directory=True,
        )
        return result

    monkeypatch.setattr(
        "apps.agent.src.agent_orchestration.plugins.skill.repository."
        "_create_temporary_file",
        create_temporary_then_exchange,
    )

    result = repo.update(
        "victim",
        skill_content("victim", body="maintenance replacement\n"),
        analysis,
    )

    detached_file = tmp_path / "workspace" / "victim-detached" / "SKILL.md"
    assert result.status == "skipped"
    assert result.target_written is False
    assert detached_file.read_text(encoding="utf-8") == original_content
    assert outside_file.read_text(encoding="utf-8") == original_content


def test_repository_replace后目录交换返回已提交且不修改workspace外文件(
    tmp_path,
    monkeypatch,
):
    workspace_file = write_skill(tmp_path / "workspace", "victim")
    outside_file = write_skill(tmp_path / "outside", "victim")
    repo = repository(tmp_path)
    analysis = repo.snapshot()
    real_replace = os.replace

    def replace_then_exchange(
        source,
        target,
        *,
        src_dir_fd=None,
        dst_dir_fd=None,
    ):
        real_replace(
            source,
            target,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
        )
        detached = workspace_file.parent.with_name("victim-detached")
        workspace_file.parent.rename(detached)
        workspace_file.parent.symlink_to(
            outside_file.parent,
            target_is_directory=True,
        )

    monkeypatch.setattr(
        "apps.agent.src.agent_orchestration.plugins.skill.repository.os.replace",
        replace_then_exchange,
    )

    result = repo.update(
        "victim",
        replacement := skill_content(
            "victim",
            body="maintenance replacement\n",
        ),
        analysis,
    )

    detached_file = tmp_path / "workspace" / "victim-detached" / "SKILL.md"
    assert result.status == "failed"
    assert result.target_written is True
    assert detached_file.read_text(encoding="utf-8") == replacement
    assert outside_file.read_text(encoding="utf-8") == skill_content("victim")


def test_repository读取期间目录交换不会读取workspace外内容(tmp_path, monkeypatch):
    workspace_file = write_skill(tmp_path / "workspace", "victim")
    outside_content = skill_content("victim", body="outside secret\n")
    outside_file = write_skill(
        tmp_path / "outside",
        "victim",
        content=outside_content,
    )
    repo = repository(tmp_path)
    original_check = (
        __import__(
            "apps.agent.src.agent_orchestration.plugins.skill.repository",
            fromlist=["_ensure_skill_directory_attached"],
        )._ensure_skill_directory_attached
    )
    exchanged = False

    def exchange_before_read(root_fd, name, skill_fd):
        nonlocal exchanged
        original_check(root_fd, name, skill_fd)
        if not exchanged:
            exchanged = True
            detached = workspace_file.parent.with_name("victim-detached")
            workspace_file.parent.rename(detached)
            workspace_file.parent.symlink_to(
                outside_file.parent,
                target_is_directory=True,
            )

    monkeypatch.setattr(
        "apps.agent.src.agent_orchestration.plugins.skill.repository."
        "_ensure_skill_directory_attached",
        exchange_before_read,
    )

    assert repo.snapshot() == ()


def test_repository实例共享workspace写锁(tmp_path):
    repo_a = repository(tmp_path)
    repo_b = repository(tmp_path)

    assert repo_a._write_lock is repo_b._write_lock


def test_repository共享锁串行create避免相互覆盖(tmp_path, monkeypatch):
    repo_a = repository(tmp_path)
    repo_b = repository(tmp_path)
    analysis = repo_a.snapshot()
    barrier = Barrier(2)
    results = []

    def create(repo, body):
        barrier.wait()
        results.append(
            repo.create(
                "same",
                skill_content("same", body=body),
                analysis,
            )
        )

    first = Thread(target=create, args=(repo_a, "first\n"))
    second = Thread(target=create, args=(repo_b, "second\n"))
    first.start()
    second.start()
    first.join()
    second.join()

    assert sorted(result.status for result in results) == [
        "skipped",
        "success",
    ]


def test_repository_merge清理部分失败返回真实副作用(tmp_path, monkeypatch):
    write_skill(tmp_path / "workspace", "source-a")
    write_skill(tmp_path / "workspace", "source-b")
    repo = repository(tmp_path)
    analysis = repo.snapshot(
        lifecycle_by_skill_key={
            "workspace:source-a": "deletion_candidate",
            "workspace:source-b": "deletion_candidate",
        }
    )
    original_remove = repo._remove_workspace_skill

    def fail_second_source(name):
        if name == "source-b":
            raise OSError("simulated cleanup failure")
        original_remove(name)

    monkeypatch.setattr(repo, "_remove_workspace_skill", fail_second_source)

    result = repo.merge(
        "merged",
        ("source-a", "source-b"),
        skill_content("merged"),
        analysis,
    )

    assert result.status == "failed"
    assert result.target_written is True
    assert result.path == (
        tmp_path / "workspace" / "merged" / "SKILL.md"
    ).absolute()
    assert result.deleted_sources == ("source-a",)
    assert result.retained_sources == ("source-b",)
    assert len(result.cleanup_errors) == 1
    assert not (tmp_path / "workspace" / "source-a").exists()
    assert (tmp_path / "workspace" / "source-b" / "SKILL.md").exists()


def test_repository_delete在unlink后fsync失败返回真实副作用(tmp_path, monkeypatch):
    skill_file = write_skill(tmp_path / "workspace", "old")
    repo = repository(tmp_path)
    analysis = repo.snapshot(
        lifecycle_by_skill_key={
            "workspace:old": "deletion_candidate",
        }
    )
    real_fsync = os.fsync
    real_unlink = os.unlink
    fail_after_unlink = False

    def failing_fsync(descriptor):
        if fail_after_unlink:
            raise OSError("simulated directory fsync failure")
        real_fsync(descriptor)

    def recording_unlink(path, *, dir_fd=None):
        nonlocal fail_after_unlink
        real_unlink(path, dir_fd=dir_fd)
        if path == "SKILL.md":
            fail_after_unlink = True

    monkeypatch.setattr(
        "apps.agent.src.agent_orchestration.plugins.skill.repository.os.fsync",
        failing_fsync,
    )
    monkeypatch.setattr(
        "apps.agent.src.agent_orchestration.plugins.skill.repository.os.unlink",
        recording_unlink,
    )

    result = repo.delete("old", analysis)

    assert result.status == "failed"
    assert result.file_deleted is True
    assert result.directory_removed is False
    assert result.deleted_sources == ("old",)
    assert result.retained_sources == ()
    assert not skill_file.exists()
    assert skill_file.parent.is_dir()


def test_repository_merge在来源unlink后失败仍标记来源已删除(tmp_path, monkeypatch):
    write_skill(tmp_path / "workspace", "source-a")
    source_b = write_skill(tmp_path / "workspace", "source-b")
    repo = repository(tmp_path)
    analysis = repo.snapshot(
        lifecycle_by_skill_key={
            "workspace:source-a": "deletion_candidate",
            "workspace:source-b": "deletion_candidate",
        }
    )
    real_fsync = os.fsync
    real_unlink = os.unlink
    fail_after_source_b_unlink = False

    def failing_fsync(descriptor):
        if fail_after_source_b_unlink:
            raise OSError("simulated source durability failure")
        real_fsync(descriptor)

    def recording_unlink(path, *, dir_fd=None):
        nonlocal fail_after_source_b_unlink
        real_unlink(path, dir_fd=dir_fd)
        if (
            path == "SKILL.md"
            and dir_fd is not None
            and os.fstat(dir_fd).st_ino == source_b.parent.stat().st_ino
        ):
            fail_after_source_b_unlink = True

    monkeypatch.setattr(
        "apps.agent.src.agent_orchestration.plugins.skill.repository.os.fsync",
        failing_fsync,
    )
    monkeypatch.setattr(
        "apps.agent.src.agent_orchestration.plugins.skill.repository.os.unlink",
        recording_unlink,
    )

    result = repo.merge(
        "merged",
        ("source-a", "source-b"),
        skill_content("merged"),
        analysis,
    )

    assert result.status == "failed"
    assert result.target_written is True
    assert result.deleted_sources == ("source-a", "source-b")
    assert result.retained_sources == ()
    assert len(result.cleanup_errors) == 1
    assert not source_b.exists()
