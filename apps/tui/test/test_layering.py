"""Enforce the TUI layer dependency direction with AST checks."""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).parents[1] / "src"

FORBIDDEN = {
    "services": {
        "textual",
        "apps.tui.src.widgets",
        "apps.tui.src.screens",
        "apps.tui.src.app",
    },
    "gateway_client": {
        "textual",
        "apps.tui.src.services",
        "apps.tui.src.widgets",
        "apps.tui.src.screens",
        "apps.tui.src.app",
    },
    "widgets": {
        "apps.tui.src.gateway_client",
        "apps.tui.src.services",
        "apps.tui.src.app",
        "apps.tui.src.platform",
    },
    "event_pipeline": {"textual"},
    "domain": {"textual", "apps.tui.src.services", "apps.tui.src.widgets", "apps.tui.src.screens", "apps.tui.src.app"},
    "configs": {"textual"},
}

NO_TEXTUAL_FILES = (
    SRC / "domain" / "chat_state.py",
    SRC / "domain" / "submission.py",
    SRC / "domain" / "commands.py",
    SRC / "configs" / "config.py",
)


def _imports(path: Path):
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name
        elif isinstance(node, ast.ImportFrom) and node.module:
            yield node.module
            if node.module == "apps.tui.src":
                for alias in node.names:
                    yield f"apps.tui.src.{alias.name}"


def _assert_no_forbidden(path: Path, forbidden: set[str]) -> None:
    for module in _imports(path):
        for prefix in forbidden:
            assert not module.startswith(prefix), (path, module)


def test_service_and_gateway_layers_do_not_import_ui() -> None:
    for folder, forbidden in FORBIDDEN.items():
        for path in (SRC / folder).rglob("*.py"):
            _assert_no_forbidden(path, forbidden)


def test_pure_model_modules_do_not_import_textual() -> None:
    for path in NO_TEXTUAL_FILES:
        _assert_no_forbidden(path, {"textual"})


def test_platform_modules_do_not_import_tui_layers() -> None:
    for path in (SRC / "platform").rglob("*.py"):
        _assert_no_forbidden(
            path,
            {
                "textual",
                "apps.tui.src.services",
                "apps.tui.src.widgets",
                "apps.tui.src.screens",
                "apps.tui.src.app",
            },
        )


def test_widgets_do_not_import_services_or_gateway() -> None:
    for path in (SRC / "widgets").rglob("*.py"):
        _assert_no_forbidden(
            path,
            {
                "apps.tui.src.gateway_client",
                "apps.tui.src.services",
                "apps.tui.src.app",
            },
        )
