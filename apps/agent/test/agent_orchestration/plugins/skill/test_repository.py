import hashlib
import os
from pathlib import Path
import stat
from threading import Barrier, Thread

import pytest

from apps.agent.src.agent_orchestration.plugins.skill.repository import (
    SkillConflictError,
    SkillRepository,
    SkillRepositoryError,
)


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
    folder: str,
    *,
    name: str | None = None,
    content: str | None = None,
) -> Path:
    skill_dir = root / folder
    skill_dir.mkdir(parents=True, mode=0o700)
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text(
        content or skill_content(name or folder),
        encoding="utf-8",
    )
    return skill_file


def repository(tmp_path: Path) -> SkillRepository:
    return SkillRepository(tmp_path / "global", tmp_path / "workspace")


@pytest.mark.parametrize("scope", ["workspace", "global"])
def test_produce_writes_requested_scope_with_safe_permissions(tmp_path, scope):
    repo = repository(tmp_path)
    content = skill_content("new-skill")

    path = repo.produce(" New-Skill ", scope, content)

    assert path == (tmp_path / scope / "new-skill" / "SKILL.md").absolute()
    assert path.read_text(encoding="utf-8") == content
    assert path.parent.stat().st_mode & 0o777 == 0o700
    assert path.stat().st_mode & 0o777 == 0o600
    assert not list(path.parent.glob(".SKILL.md.*.tmp"))


@pytest.mark.parametrize("existing_scope", ["workspace", "global"])
def test_produce_rejects_conflict_in_either_scope_without_side_effect(
    tmp_path, existing_scope
):
    existing = write_skill(tmp_path / existing_scope, "shared")
    original = existing.read_text(encoding="utf-8")
    repo = repository(tmp_path)

    with pytest.raises(SkillConflictError, match="already exists"):
        repo.produce("SHARED", "workspace", skill_content("shared", body="new\n"))

    assert existing.read_text(encoding="utf-8") == original
    if existing_scope == "global":
        assert not (tmp_path / "workspace" / "shared").exists()


def test_produce_detects_normalized_name_conflict_in_different_folder(tmp_path):
    write_skill(tmp_path / "global", "legacy-folder", name="Target")
    repo = repository(tmp_path)

    with pytest.raises(SkillConflictError, match="already exists"):
        repo.produce("target", "workspace", skill_content("target"))


def test_find_conflicts_includes_invalid_same_name_physical_entry(tmp_path):
    invalid_dir = tmp_path / "workspace" / "occupied"
    invalid_dir.mkdir(parents=True)
    (invalid_dir / "SKILL.md").write_text("not valid", encoding="utf-8")
    repo = repository(tmp_path)

    assert repo.find_conflicts("occupied") == ("workspace",)
    assert repo.find_conflicts("missing") == ()


def test_workspace_evolve_updates_exact_snapshot(tmp_path):
    original = write_skill(tmp_path / "workspace", "shared")
    repo = repository(tmp_path)
    snapshot = repo.capture("shared")
    assert snapshot is not None
    replacement = skill_content("shared", body="evolved\n")

    path = repo.evolve(snapshot, replacement)

    assert path == original.absolute()
    assert original.read_text(encoding="utf-8") == replacement


def test_global_evolve_creates_workspace_override_without_modifying_global(tmp_path):
    global_file = write_skill(
        tmp_path / "global",
        "shared",
        content=skill_content("shared", body="global\n"),
    )
    repo = repository(tmp_path)
    snapshot = repo.capture("shared")
    assert snapshot is not None and snapshot.scope == "global"
    replacement = skill_content("shared", body="workspace override\n")

    path = repo.evolve(snapshot, replacement)

    assert global_file.read_text(encoding="utf-8").endswith("global\n")
    assert path == (tmp_path / "workspace" / "shared" / "SKILL.md").absolute()
    assert path.read_text(encoding="utf-8") == replacement


def test_evolve_rejects_hash_change_and_workspace_override_race(tmp_path):
    workspace_file = write_skill(tmp_path / "workspace", "workspace-skill")
    global_file = write_skill(tmp_path / "global", "global-skill")
    repo = repository(tmp_path)
    workspace_snapshot = repo.capture("workspace-skill")
    global_snapshot = repo.capture("global-skill")
    assert workspace_snapshot is not None and global_snapshot is not None
    workspace_file.write_text(
        skill_content("workspace-skill", body="concurrent\n"),
        encoding="utf-8",
    )
    override = write_skill(tmp_path / "workspace", "global-skill")

    with pytest.raises(SkillConflictError, match="changed after analysis"):
        repo.evolve(workspace_snapshot, skill_content("workspace-skill"))
    with pytest.raises(SkillConflictError, match="override.*appeared"):
        repo.evolve(global_snapshot, skill_content("global-skill"))

    assert global_file.exists()
    assert override.exists()


@pytest.mark.parametrize(
    ("name", "scope", "content"),
    [
        ("../escape", "workspace", skill_content("escape")),
        ("safe", "session", skill_content("safe")),
        ("safe", "workspace", "# no front matter\n"),
        ("safe", "workspace", skill_content("different")),
        ("safe", "workspace", "---\nname: safe\ndescription:\n---\n"),
    ],
)
def test_produce_rejects_invalid_target_or_content(tmp_path, name, scope, content):
    repo = repository(tmp_path)

    with pytest.raises(SkillRepositoryError):
        repo.produce(name, scope, content)

    assert not (tmp_path / "workspace" / "safe" / "SKILL.md").exists()


def test_repository_rejects_symlink_target_without_touching_outside(tmp_path):
    outside_file = write_skill(tmp_path / "outside", "escaped")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "escaped").symlink_to(outside_file.parent, target_is_directory=True)
    repo = SkillRepository(tmp_path / "global", workspace)

    with pytest.raises(SkillConflictError):
        repo.produce("escaped", "workspace", skill_content("escaped"))

    assert outside_file.read_text(encoding="utf-8") == skill_content("escaped")


def test_capture_returns_hash_and_visible_workspace_override(tmp_path):
    write_skill(tmp_path / "global", "shared", content=skill_content("shared", body="global\n"))
    workspace_file = write_skill(
        tmp_path / "workspace",
        "shared",
        content=skill_content("shared", body="workspace\n"),
    )
    repo = repository(tmp_path)

    snapshot = repo.capture("shared")

    assert snapshot is not None
    assert snapshot.scope == "workspace"
    assert snapshot.path == workspace_file.absolute()
    assert snapshot.content_hash == hashlib.sha256(
        snapshot.content.encode("utf-8")
    ).hexdigest()
    assert repo.capture("missing") is None


def test_atomic_write_uses_same_directory_replace_and_fsync(tmp_path, monkeypatch):
    repo = repository(tmp_path)
    real_replace = os.replace
    real_fsync = os.fsync
    replace_calls = []
    fsync_calls = []

    def recording_replace(source, target, *, src_dir_fd=None, dst_dir_fd=None):
        replace_calls.append((Path(source), Path(target), src_dir_fd, dst_dir_fd))
        real_replace(source, target, src_dir_fd=src_dir_fd, dst_dir_fd=dst_dir_fd)

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

    path = repo.produce("atomic", "workspace", skill_content("atomic"))

    assert path.exists()
    assert len(replace_calls) == 1
    temporary, target, source_fd, target_fd = replace_calls[0]
    assert source_fd == target_fd
    assert temporary.parent == target.parent
    assert target.name == "SKILL.md"
    assert any(stat.S_ISDIR(item.st_mode) for item in fsync_calls)


def test_repository_instances_share_lock_and_concurrent_produce_does_not_overwrite(tmp_path):
    repo_a = repository(tmp_path)
    repo_b = repository(tmp_path)
    assert repo_a._write_lock is repo_b._write_lock
    barrier = Barrier(2)
    results = []

    def produce(repo, body):
        barrier.wait()
        try:
            results.append(repo.produce("same", "workspace", skill_content("same", body=body)))
        except SkillConflictError as error:
            results.append(error)

    threads = [
        Thread(target=produce, args=(repo_a, "first\n")),
        Thread(target=produce, args=(repo_b, "second\n")),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sum(isinstance(result, Path) for result in results) == 1
    assert sum(isinstance(result, SkillConflictError) for result in results) == 1
