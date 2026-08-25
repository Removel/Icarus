import os
from pathlib import Path
from threading import Barrier, Thread

import pytest
import apps.agent.src.agent_orchestration.plugins.skill.repository as repository_module

from apps.agent.src.agent_orchestration.plugins.skill.repository import (
    MAX_FILE_BYTES,
    SkillConflictError,
    SkillRepository,
    SkillRepositoryError,
    SkillSecurityError,
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


def prepare(repo: SkillRepository, name: str, scope="workspace") -> Path:
    draft = repo.prepare_produce(name, scope)
    (draft / "SKILL.md").write_text(skill_content(name), encoding="utf-8")
    return draft


@pytest.mark.parametrize("scope", ["workspace", "global"])
def test_produce_publishes_complete_draft_with_binary_assets(tmp_path, scope):
    repo = repository(tmp_path)
    draft = prepare(repo, "new-skill", scope)
    (draft / "scripts").mkdir()
    (draft / "scripts" / "check.py").write_text("print('ok')\n")
    (draft / "assets").mkdir()
    binary = b"\x00\xff\x89PNG\r\n"
    (draft / "assets" / "icon.bin").write_bytes(binary)

    path = repo.publish_produce(" New-Skill ", scope, draft)

    assert path == (tmp_path / scope / "new-skill" / "SKILL.md").absolute()
    assert path.read_text(encoding="utf-8") == skill_content("new-skill")
    assert (path.parent / "scripts" / "check.py").exists()
    assert (path.parent / "assets" / "icon.bin").read_bytes() == binary
    assert not draft.exists()


@pytest.mark.parametrize("existing_scope", ["workspace", "global"])
def test_produce_rejects_conflict_without_consuming_draft(
    tmp_path, existing_scope
):
    existing = write_skill(tmp_path / existing_scope, "shared")
    repo = repository(tmp_path)
    draft = prepare(repo, "shared")

    with pytest.raises(SkillConflictError, match="already exists"):
        repo.publish_produce("SHARED", "workspace", draft)

    assert existing.exists()
    assert draft.exists()


def test_find_conflicts_includes_invalid_same_name_entry(tmp_path):
    occupied = tmp_path / "workspace" / "occupied"
    occupied.mkdir(parents=True)
    (occupied / "SKILL.md").write_text("invalid", encoding="utf-8")
    repo = repository(tmp_path)

    assert repo.find_conflicts("occupied") == ("workspace",)
    assert repo.find_conflicts("missing") == ()


def test_workspace_evolve_replaces_complete_directory(tmp_path):
    original = write_skill(tmp_path / "workspace", "shared")
    (original.parent / "keep.txt").write_text("keep")
    (original.parent / "remove.txt").write_text("remove")
    repo = repository(tmp_path)
    snapshot = repo.capture("shared")
    assert snapshot is not None
    draft = repo.prepare_evolve(snapshot)
    (draft / "SKILL.md").write_text(
        skill_content("shared", body="evolved\n"), encoding="utf-8"
    )
    (draft / "remove.txt").unlink()
    (draft / "new.bin").write_bytes(b"\x00new")

    path = repo.publish_evolve(snapshot, draft)

    assert path == original.absolute()
    assert path.read_text().endswith("evolved\n")
    assert (path.parent / "keep.txt").read_text() == "keep"
    assert not (path.parent / "remove.txt").exists()
    assert (path.parent / "new.bin").read_bytes() == b"\x00new"


def test_global_evolve_creates_workspace_override(tmp_path):
    global_file = write_skill(
        tmp_path / "global",
        "shared",
        content=skill_content("shared", body="global\n"),
    )
    (global_file.parent / "asset.bin").write_bytes(b"asset")
    repo = repository(tmp_path)
    snapshot = repo.capture("shared")
    assert snapshot is not None and snapshot.scope == "global"
    draft = repo.prepare_evolve(snapshot)
    (draft / "SKILL.md").write_text(
        skill_content("shared", body="workspace override\n")
    )

    path = repo.publish_evolve(snapshot, draft)

    assert global_file.read_text().endswith("global\n")
    assert path == (tmp_path / "workspace" / "shared" / "SKILL.md").absolute()
    assert (path.parent / "asset.bin").read_bytes() == b"asset"


def test_evolve_rejects_any_directory_change_and_override_race(tmp_path):
    workspace_file = write_skill(tmp_path / "workspace", "workspace-skill")
    global_file = write_skill(tmp_path / "global", "global-skill")
    repo = repository(tmp_path)
    workspace_snapshot = repo.capture("workspace-skill")
    global_snapshot = repo.capture("global-skill")
    assert workspace_snapshot is not None and global_snapshot is not None
    workspace_draft = repo.prepare_evolve(workspace_snapshot)
    global_draft = repo.prepare_evolve(global_snapshot)
    (workspace_file.parent / "concurrent.txt").write_text("changed")
    override = write_skill(tmp_path / "workspace", "global-skill")

    with pytest.raises(SkillConflictError, match="changed after analysis"):
        repo.publish_evolve(workspace_snapshot, workspace_draft)
    with pytest.raises(SkillConflictError, match="override.*appeared"):
        repo.publish_evolve(global_snapshot, global_draft)

    assert global_file.exists() and override.exists()


def test_prepare_evolve_rejects_source_change_during_copy(tmp_path, monkeypatch):
    source = write_skill(tmp_path / "workspace", "shared")
    repo = repository(tmp_path)
    snapshot = repo.capture("shared")
    assert snapshot is not None
    real_copy = repository_module._copy_skill_tree

    def mutate_then_copy(source_dir, destination):
        real_copy(source_dir, destination)
        source.write_text(skill_content("shared", body="changed\n"))

    monkeypatch.setattr(repository_module, "_copy_skill_tree", mutate_then_copy)

    with pytest.raises(SkillConflictError, match="preparing Draft"):
        repo.prepare_evolve(snapshot)

    assert list((tmp_path / "workspace" / ".drafts").iterdir()) == []


@pytest.mark.parametrize(
    "mutate",
    [
        lambda draft: (draft / "SKILL.md").unlink(),
        lambda draft: (draft / "SKILL.md").write_text("not valid"),
        lambda draft: (draft / "SKILL.md").write_text(skill_content("different")),
        lambda draft: (draft / "large.bin").write_bytes(b"x" * (MAX_FILE_BYTES + 1)),
    ],
)
def test_publish_rejects_invalid_draft(tmp_path, mutate):
    repo = repository(tmp_path)
    draft = prepare(repo, "safe")
    mutate(draft)

    with pytest.raises(SkillRepositoryError):
        repo.publish_produce("safe", "workspace", draft)

    assert not (tmp_path / "workspace" / "safe").exists()


def test_publish_rejects_symlink_inside_draft(tmp_path):
    repo = repository(tmp_path)
    draft = prepare(repo, "safe")
    outside = tmp_path / "outside.txt"
    outside.write_text("outside")
    (draft / "link").symlink_to(outside)

    with pytest.raises(SkillSecurityError, match="symlink"):
        repo.publish_produce("safe", "workspace", draft)

    assert outside.read_text() == "outside"


def test_capture_returns_directory_hash_and_workspace_override(tmp_path):
    write_skill(tmp_path / "global", "shared")
    workspace_file = write_skill(tmp_path / "workspace", "shared")
    (workspace_file.parent / "extra.txt").write_text("extra")
    repo = repository(tmp_path)

    snapshot = repo.capture("shared")

    assert snapshot is not None
    assert snapshot.scope == "workspace"
    assert snapshot.path == workspace_file.absolute()
    assert len(snapshot.directory_hash) == 64
    assert repo.capture("missing") is None


def test_capture_rejects_directory_exchanged_during_validation(
    tmp_path, monkeypatch
):
    original = write_skill(tmp_path / "workspace", "shared")
    repo = repository(tmp_path)
    real_validate = repository_module._validate_skill_tree

    def exchange_then_validate(root, target_name):
        detached = root.with_name("detached")
        root.rename(detached)
        write_skill(root.parent, root.name)
        return real_validate(root, target_name)

    monkeypatch.setattr(
        repository_module, "_validate_skill_tree", exchange_then_validate
    )

    with pytest.raises(SkillConflictError, match="exchanged"):
        repo.capture("shared")

    assert original.parent.name == "shared"


def test_cleanup_only_accepts_repository_drafts(tmp_path):
    repo = repository(tmp_path)
    draft = prepare(repo, "safe")
    repo.cleanup_draft(draft)
    repo.cleanup_draft(draft)

    outside = tmp_path / "outside"
    outside.mkdir()
    with pytest.raises(SkillSecurityError, match="unsafe Draft"):
        repo.cleanup_draft(outside)


def test_repository_instances_share_lock_and_concurrent_publish_does_not_overwrite(tmp_path):
    repo_a = repository(tmp_path)
    repo_b = repository(tmp_path)
    assert repo_a._write_lock is repo_b._write_lock
    draft_a = prepare(repo_a, "same")
    draft_b = prepare(repo_b, "same")
    (draft_a / "value").write_text("first")
    (draft_b / "value").write_text("second")
    barrier = Barrier(2)
    results = []

    def publish(repo, draft):
        barrier.wait()
        try:
            results.append(repo.publish_produce("same", "workspace", draft))
        except SkillConflictError as error:
            results.append(error)

    threads = [
        Thread(target=publish, args=(repo_a, draft_a)),
        Thread(target=publish, args=(repo_b, draft_b)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sum(isinstance(result, Path) for result in results) == 1
    assert sum(isinstance(result, SkillConflictError) for result in results) == 1


def test_workspace_evolve_rolls_back_if_new_directory_move_fails(tmp_path, monkeypatch):
    original = write_skill(tmp_path / "workspace", "shared")
    original_text = original.read_text()
    repo = repository(tmp_path)
    snapshot = repo.capture("shared")
    assert snapshot is not None
    draft = repo.prepare_evolve(snapshot)
    (draft / "SKILL.md").write_text(skill_content("shared", body="new\n"))
    real_rename = os.rename
    calls = 0

    def fail_second_rename(source, target):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated publish failure")
        return real_rename(source, target)

    monkeypatch.setattr(
        "apps.agent.src.agent_orchestration.plugins.skill.repository.os.rename",
        fail_second_rename,
    )

    with pytest.raises(OSError, match="simulated"):
        repo.publish_evolve(snapshot, draft)

    assert original.read_text() == original_text


def test_workspace_evolve_keeps_success_if_backup_cleanup_fails(
    tmp_path, monkeypatch, caplog
):
    original = write_skill(tmp_path / "workspace", "shared")
    repo = repository(tmp_path)
    snapshot = repo.capture("shared")
    assert snapshot is not None
    draft = repo.prepare_evolve(snapshot)
    (draft / "SKILL.md").write_text(skill_content("shared", body="new\n"))

    def fail_cleanup(path, *args, **kwargs):
        raise OSError("simulated backup cleanup failure")

    monkeypatch.setattr(repository_module.shutil, "rmtree", fail_cleanup)

    path = repo.publish_evolve(snapshot, draft)

    assert path.read_text().endswith("new\n")
    assert "Unable to remove committed Skill backup" in caplog.text
