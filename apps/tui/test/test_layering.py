"""Imports enforce app-owned UI, pure data and object-scoped external clients."""

import ast
import importlib.util
from pathlib import Path

SRC = Path(__file__).parents[1] / "src"
UI = {"textual", "apps.tui.src.widgets", "apps.tui.src.screens", "apps.tui.src.app"}
RULES = {
    "client": UI
    | {"apps.tui.src.services", "apps.tui.src.stores", "apps.tui.src.viewmodels"},
    "services": UI,
    "domain": UI
    | {
        "apps.tui.src.client",
        "apps.tui.src.services",
        "apps.tui.src.platform",
        "apps.tui.src.stores",
    },
    "platform": UI | {"apps.tui.src.services"},
    "stores": UI
    | {"apps.tui.src.client", "apps.tui.src.services", "apps.tui.src.platform"},
    "viewmodels": UI
    | {"apps.tui.src.client", "apps.tui.src.services", "apps.tui.src.platform"},
    "configs": UI | {"apps.tui.src.services", "apps.tui.src.client"},
    "event_pipeline": UI,
    "widgets": {
        "apps.tui.src.client",
        "apps.tui.src.services",
        "apps.tui.src.platform",
        "apps.tui.src.app",
    },
}


def imports(path):
    package = "apps.tui.src." + ".".join(path.parent.relative_to(SRC).parts)
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level:
                module = importlib.util.resolve_name("." * node.level + module, package)
            yield module
            for alias in node.names:
                yield module + "." + alias.name


def test_import_directions():
    for folder, forbidden in RULES.items():
        for path in (SRC / folder).rglob("*.py"):
            for name in imports(path):
                assert not any(
                    name == prefix or name.startswith(prefix + ".")
                    for prefix in forbidden
                ), (path, name)


def test_domain_performs_no_attachment_io():
    for path in (SRC / "domain").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                assert node.func.attr not in {
                    "unlink",
                    "mkdir",
                    "write_bytes",
                    "write_text",
                }, (path, node.func.attr)
