"""Framework and plugin implementations obey their declared dependency direction."""

import ast
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[2] / "src"


def imports(path):
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield node.lineno, alias.name
        elif isinstance(node, ast.ImportFrom):
            yield node.lineno, node.module or ""


def test_core_and_runtime_have_no_application_imports():
    core, runtime = SOURCE / "icarus/core", SOURCE / "icarus/runtime"
    assert core.is_dir()
    assert runtime.is_dir()
    violations = []
    for package in (core, runtime):
        for path in package.rglob("*.py"):
            for line, module in imports(path):
                if module.startswith(("agent_app", "apps.agent", "openai", "anthropic")):
                    violations.append((str(path.relative_to(SOURCE)), line, module))
                if package == core and module.startswith("icarus.runtime"):
                    violations.append((str(path.relative_to(SOURCE)), line, module))
    assert violations == []


def test_plugins_do_not_import_sibling_implementations_and_utils_are_leaves():
    violations = []
    plugins = SOURCE / "agent_app/plugins"
    for path in plugins.rglob("*.py"):
        own_plugin = path.relative_to(plugins).parts[0]
        for line, module in imports(path):
            if module.startswith("agent_app.plugins.") and module.split(".")[2] != own_plugin:
                violations.append((str(path.relative_to(SOURCE)), line, module))
    for path in (SOURCE / "agent_app/utils").rglob("*.py"):
        for line, module in imports(path):
            if module.startswith(("agent_app.plugins", "agent_app.application")):
                violations.append((str(path.relative_to(SOURCE)), line, module))
    assert violations == []
