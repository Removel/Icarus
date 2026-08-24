import logging

import pytest

from apps.agent.src.agent_orchestration.plugins.skill import SkillScanner


def write_skill(directory, folder, name, description, keywords=None):
    skill_dir = directory / folder
    skill_dir.mkdir(parents=True)
    keyword_yaml = ""
    if keywords is not None:
        keyword_yaml = f"keywords: {keywords}\n"
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n{keyword_yaml}---\nbody\n",
        encoding="utf-8",
    )


def test_scanner_workspace_override_and_physical_scans(tmp_path):
    global_dir = tmp_path / "global"
    workspace_dir = tmp_path / "workspace"
    write_skill(global_dir, "z", "Zulu", "global zulu")
    write_skill(global_dir, "shared", "Shared", "global shared")
    write_skill(workspace_dir, "a", "alpha", "workspace alpha")
    write_skill(workspace_dir, "shared", "SHARED", "workspace shared")

    scanner = SkillScanner(global_dir, workspace_dir)

    assert [skill.normalized_name for skill in scanner.scan()] == [
        "alpha",
        "shared",
        "zulu",
    ]
    shared = scanner.scan()[1]
    assert shared.scope == "workspace"
    assert shared.description == "workspace shared"
    assert [skill.normalized_name for skill in scanner.scan_scope("global")] == [
        "shared",
        "zulu",
    ]
    assert [
        skill.normalized_name for skill in scanner.scan_scope("workspace")
    ] == ["alpha", "shared"]


def test_scanner_parses_keywords_and_ignores_invalid_optional_field(
    tmp_path, caplog
):
    global_dir = tmp_path / "global"
    workspace_dir = tmp_path / "workspace"
    write_skill(global_dir, "valid", "Valid", "valid", "[Python, unit test]")
    write_skill(global_dir, "invalid", "Invalid", "still visible", "oops")

    with caplog.at_level(logging.WARNING):
        skills = SkillScanner(global_dir, workspace_dir).scan()

    assert skills[0].keywords == ()
    assert skills[1].keywords == ("Python", "unit test")
    assert "Ignoring invalid Skill keywords" in caplog.text


def test_scanner_skips_invalid_and_duplicate_definitions(tmp_path, caplog):
    global_dir = tmp_path / "global"
    workspace_dir = tmp_path / "workspace"
    write_skill(global_dir, "first", "Same", "first")
    write_skill(global_dir, "second", "same", "second")
    invalid = global_dir / "invalid"
    invalid.mkdir(parents=True)
    (invalid / "SKILL.md").write_text(
        "---\nname: missing-description\n---\n",
        encoding="utf-8",
    )

    with caplog.at_level(logging.WARNING):
        skills = SkillScanner(global_dir, workspace_dir).scan()

    assert len(skills) == 1
    assert skills[0].description == "first"
    assert "Skipping duplicate" in caplog.text
    assert "Skipping invalid" in caplog.text


def test_scanner_skips_symlink_that_resolves_outside_scope(tmp_path, caplog):
    global_dir = tmp_path / "global"
    workspace_dir = tmp_path / "workspace"
    outside = tmp_path / "outside"
    write_skill(outside, "escaped", "Escaped", "must not be visible")
    global_dir.mkdir()
    (global_dir / "escaped").symlink_to(outside / "escaped", target_is_directory=True)

    with caplog.at_level(logging.WARNING):
        skills = SkillScanner(global_dir, workspace_dir).scan()

    assert skills == []
    assert "outside global root" in caplog.text


def test_scanner_missing_directories_and_invalid_scope(tmp_path):
    scanner = SkillScanner(tmp_path / "a", tmp_path / "b")

    assert scanner.scan() == []
    with pytest.raises(ValueError, match="Unsupported Skill scope"):
        scanner.scan_scope("all")
