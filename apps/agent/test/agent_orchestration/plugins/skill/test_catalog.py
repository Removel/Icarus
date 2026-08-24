import inspect

import pytest

from apps.agent.src.agent_orchestration.plugins.skill import (
    SkillCatalog,
    SkillDefinition,
    SkillScanner,
)


def write_skill(directory, folder, name, description, keywords=None):
    skill_dir = directory / folder
    skill_dir.mkdir(parents=True)
    keyword_yaml = "" if keywords is None else f"keywords: {keywords}\n"
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n{keyword_yaml}---\nbody\n",
        encoding="utf-8",
    )


def make_catalog(tmp_path):
    global_dir = tmp_path / "global"
    workspace_dir = tmp_path / "workspace"
    write_skill(
        global_dir,
        "python",
        "python-unit_test-workflow",
        "Async test conventions",
        "[pytest, coroutine]",
    )
    write_skill(global_dir, "shared", "shared", "global copy")
    write_skill(workspace_dir, "shared", "SHARED", "workspace copy")
    write_skill(
        workspace_dir,
        "literal",
        "literal-search",
        "Handles a+b and [test] literally",
    )
    return SkillCatalog(SkillScanner(global_dir, workspace_dir))


def test_list_scope_override_fields_and_stable_order(tmp_path):
    catalog = make_catalog(tmp_path)

    visible = catalog.list_skills()
    assert [skill.normalized_name for skill in visible] == [
        "literal-search",
        "python-unit_test-workflow",
        "shared",
    ]
    assert visible[-1].scope == "workspace"
    assert visible[-1].path.is_absolute()
    assert [skill.scope for skill in catalog.list_skills("global")] == [
        "global",
        "global",
    ]
    assert [skill.scope for skill in catalog.list_skills("workspace")] == [
        "workspace",
        "workspace",
    ]
    with pytest.raises(ValueError, match="Unsupported Skill catalog scope"):
        catalog.list_skills("session")


def test_catalog_method_annotations_can_be_evaluated():
    assert inspect.signature(SkillCatalog.search).return_annotation == list[
        SkillDefinition
    ]


def test_search_normalizes_case_whitespace_hyphen_and_underscore(tmp_path):
    catalog = make_catalog(tmp_path)

    assert [skill.name for skill in catalog.search(["PYTHON   UNIT-TEST"])] == [
        "python-unit_test-workflow"
    ]


def test_search_matches_any_keyword_and_prioritizes_match_count_and_field(tmp_path):
    global_dir = tmp_path / "global"
    workspace_dir = tmp_path / "workspace"
    write_skill(global_dir, "name", "python-async", "plain")
    write_skill(global_dir, "metadata", "metadata", "plain", "[python, async]")
    write_skill(global_dir, "description", "description", "python async")
    write_skill(global_dir, "partial", "python-only", "plain")
    catalog = SkillCatalog(SkillScanner(global_dir, workspace_dir))

    assert [skill.name for skill in catalog.search(["python", "async"])] == [
        "python-async",
        "metadata",
        "description",
        "python-only",
    ]


def test_search_treats_regex_metacharacters_as_literals(tmp_path):
    catalog = make_catalog(tmp_path)

    assert [skill.name for skill in catalog.search(["a+b"])] == [
        "literal-search"
    ]
    assert catalog.search(["a.*b"]) == []


def test_search_limit_empty_result_and_validation(tmp_path):
    global_dir = tmp_path / "global"
    workspace_dir = tmp_path / "workspace"
    for index in range(12):
        write_skill(global_dir, str(index), f"match-{index:02d}", "target")
    catalog = SkillCatalog(SkillScanner(global_dir, workspace_dir))

    assert len(catalog.search(["target"])) == 10
    assert catalog.search(["missing"]) == []
    for keywords in ([], [""], ["x"] * 9, "target"):
        with pytest.raises(ValueError):
            catalog.search(keywords)


def test_find_visible_resolves_workspace_override(tmp_path):
    catalog = make_catalog(tmp_path)

    visible = catalog.find_visible(" shared " )
    assert visible is not None
    assert visible.scope == "workspace"
    assert catalog.find_visible("missing") is None
    with pytest.raises(ValueError):
        catalog.find_visible("  " )
