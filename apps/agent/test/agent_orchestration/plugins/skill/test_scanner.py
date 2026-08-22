import logging

from apps.agent.src.agent_orchestration.plugins.skill import SkillScanner


def write_skill(directory, folder, name, description, extra=""):
    skill_dir = directory / folder
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n{extra}---\nbody\n",
        encoding="utf-8",
    )


def test_scanner_workspace同名覆盖global且稳定排序(tmp_path):
    global_dir = tmp_path / "global"
    workspace_dir = tmp_path / "workspace"
    write_skill(global_dir, "z", "Zulu", "global zulu")
    write_skill(global_dir, "shared", "Shared", "global shared")
    write_skill(workspace_dir, "a", "alpha", "workspace alpha")
    write_skill(workspace_dir, "shared", "SHARED", "workspace shared")

    skills = SkillScanner(global_dir, workspace_dir).scan()

    assert [skill.normalized_name for skill in skills] == [
        "alpha",
        "shared",
        "zulu",
    ]
    shared = skills[1]
    assert shared.scope == "workspace"
    assert shared.description == "workspace shared"
    assert shared.metadata["name"] == "SHARED"


def test_scanner跳过非法和同scope重复定义(tmp_path, caplog):
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


def test_scanner目录不存在返回空列表(tmp_path):
    assert SkillScanner(tmp_path / "a", tmp_path / "b").scan() == []
