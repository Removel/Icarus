import importlib
import logging

import pytest


def load(root, max_tokens=100):
    try:
        module = importlib.import_module("agent_app.plugins.prompt.sources")
    except ModuleNotFoundError:
        pytest.fail("Workspace prompt discovery is not implemented")
    return module.load_workspace_section(root, max_tokens=max_tokens, logger=logging.getLogger("test"))


def test_workspace_agents_precedes_claude_without_combining(tmp_path):
    (tmp_path / "CLAUDE.md").write_text("CLAUDE")
    assert load(tmp_path).content == "CLAUDE"
    (tmp_path / "AGENTS.md").write_text("\n AGENTS \n")
    assert load(tmp_path).content == "\n AGENTS \n"


@pytest.mark.parametrize("kind", ["empty", "bad_utf8", "too_large", "directory"])
def test_invalid_existing_agents_does_not_fall_back(tmp_path, kind):
    (tmp_path / "CLAUDE.md").write_text("FALLBACK")
    target = tmp_path / "AGENTS.md"
    if kind == "empty":
        target.write_text("")
    elif kind == "bad_utf8":
        target.write_bytes(b"\xff")
    elif kind == "too_large":
        target.write_text("x" * 101)
    else:
        target.mkdir()
    assert load(tmp_path) is None


def test_missing_files_are_optional_and_nested_files_are_not_discovered(tmp_path):
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "AGENTS.md").write_text("NESTED")
    assert load(tmp_path) is None


def test_workspace_symlink_cannot_read_outside_root(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    private = tmp_path / "private.md"
    private.write_text("PRIVATE")
    (workspace / "AGENTS.md").symlink_to(private)
    assert load(workspace) is None
