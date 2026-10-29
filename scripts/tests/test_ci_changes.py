"""Behavioral contract for conservative CI impact selection."""

import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]


def changes_module():
    path = ROOT / "scripts/ci/changes.py"
    assert path.is_file(), "CI must compute and expose an explicit affected task set"
    spec = importlib.util.spec_from_file_location("ci_changes", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("path", [
    "icarus.toml", "bin/icarus", "infra/postgres/compose.yaml",
    "packages/gateway_protocol/models.py", ".github/workflows/ci.yml",
    "scripts/ci/changes.py", "apps/new-app/src/main.py", "new-config.toml",
])
def test_shared_or_unknown_inputs_conservatively_select_all(path):
    changes = changes_module()
    result = changes.select_tasks([path])
    assert set(result["expected"]) == set(changes.ALL_TASKS)
    assert set(result["images"]) == {"mem0", "openkb", "webui", "rela-mem"}


@pytest.mark.parametrize("path,required", [
    ("apps/tui/src/app.py", {"syntax", "tui"}),
    ("apps/webui/pnpm-lock.yaml", {"syntax", "webui", "images"}),
    ("apps/agent/src/icarus/core/tools/tool_surface.py", {"syntax", "backend", "webui"}),
    ("apps/gateway/src/main.py", {"syntax", "backend", "tui", "webui"}),
    ("apps/rela-mem/pyproject.toml", {"syntax", "services", "backend", "webui", "images"}),
])
def test_application_inputs_include_consumers_and_artifacts(path, required):
    result = changes_module().select_tasks([path])
    assert required <= set(result["expected"])


def test_tui_only_does_not_install_unrelated_services_or_build_images():
    result = changes_module().select_tasks(["apps/tui/test/test_layering.py"])
    assert set(result["expected"]) == {"syntax", "tui"}
    assert result["images"] == []
    assert result["service_apps"] == []


def test_only_explicit_documentation_paths_can_skip_application_tests():
    changes = changes_module()
    assert changes.select_tasks(["docs/spec/2026-10-29-ci-quality-gates/plan.md"])["expected"] == ["syntax"]
    assert "backend" in changes.select_tasks(["apps/agent/src/agent_app/resources/personas/default/persona.md"])["expected"]


def test_rename_parser_retains_both_paths_and_non_ascii_names():
    paths = changes_module().changed_paths("R100\0apps/gateway/old name.py\0docs/renamed-文.md\0D\0icarus.toml\0".encode("utf-8"))
    assert paths == ["apps/gateway/old name.py", "docs/renamed-文.md", "icarus.toml"]
    assert "backend" in changes_module().select_tasks(paths)["expected"]


def test_malformed_change_list_fails_instead_of_returning_empty_selection():
    with pytest.raises(ValueError):
        changes_module().changed_paths(b"R100\0only-one-name\0")


def test_empty_changes_still_select_syntax():
    assert changes_module().select_tasks([])["expected"] == ["syntax"]


def test_new_branch_event_forces_full_verification():
    module = changes_module()
    assert module.event_range("push", {"before": "0" * 40, "after": "a" * 40}) == (None, "a" * 40, False)


def test_pr_uses_base_and_head_for_impact_analysis():
    module = changes_module()
    assert module.event_range("pull_request", {"pull_request": {
        "base": {"sha": "base"}, "head": {"sha": "head"},
    }}) == ("base", "head", True)
