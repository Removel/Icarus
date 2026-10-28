"""Focused message widgets rendered inside the conversation view."""

from __future__ import annotations

from pathlib import Path

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Label, Static

ICARUS_LOGO = """ ░▒▒░     ░▒▓▓▓▓▒░         ▒▒▒░      ░▒▒▒▒▒▒▒▒░    ░▒▒░      ░▒▒░    ░▒▓▓▓▒▒
 ▒██▒   ▒███▓▓▓▓███▒      ▓████      ▒██▓▓▓▓▓███▒  ▒██▒      ▒██▒  ▒███▓▒▓▓██▓
 ▒██▒  ▓██▓      ▓██▒    ▒██░██▓     ▒██▒     ███  ▒██▒      ▒██▒  ███     ░▓▓░
 ▒██▒ ░███              ░██▓ ░██▒    ▒██▒     ███  ▒██▒      ▒██▒  ▓██▓▒░░
 ▒██▒ ░██▓              ███   ▓██    ▒███▓▓▓████░  ▒██▒      ▒██▒   ░▒▓██████▒
 ▒██▒  ███        ░░░  ▓██████████   ▒██▓▒▒▓██▓    ▒██▒      ▒██▒         ░▓██▓
 ▒██▒  ▒██▓░     ▓██░ ░██▓░░░░░▒██▒  ▒██▒   ▒██▓   ░███░    ░███░ ░██▒     ░███
 ▒██▒   ░▓████████▓░  ███       ▓██░ ▒██▒    ▒██▓   ░▓████████▓░   ▒████▓▓███▓
  ░░       ░▒▒▒▒░     ░░░        ░░░  ░░      ░░░░     ░▒▒▒▒░        ░░▒▒▒▒░"""

_LOGO_TOP_LEFT = (184, 184, 191)
_LOGO_TOP_RIGHT = (218, 117, 129)
_LOGO_BOTTOM_LEFT = (185, 182, 189)
_LOGO_BOTTOM_RIGHT = (219, 115, 127)
_LOGO_CANVAS_WIDTH = 80
_LOGO_CANVAS_HEIGHT = 15
_LOGO_FIRST_VISIBLE_ROW = 1


def _mix_color(
    start: tuple[int, int, int],
    end: tuple[int, int, int],
    ratio: float,
) -> tuple[int, int, int]:
    return tuple(
        round(left + (right - left) * ratio) for left, right in zip(start, end)
    )


def render_icarus_logo() -> Text:
    """Render the art.txt logo with its silver-to-pink true-color gradient."""

    lines = ICARUS_LOGO.splitlines()
    rendered = Text(no_wrap=True)
    for row_index, line in enumerate(lines):
        source_row = row_index + _LOGO_FIRST_VISIBLE_ROW
        vertical_ratio = source_row / (_LOGO_CANVAS_HEIGHT - 1)
        left = _mix_color(_LOGO_TOP_LEFT, _LOGO_BOTTOM_LEFT, vertical_ratio)
        right = _mix_color(_LOGO_TOP_RIGHT, _LOGO_BOTTOM_RIGHT, vertical_ratio)
        for column, character in enumerate(line):
            if character == " ":
                rendered.append(character)
                continue
            horizontal_ratio = column / (_LOGO_CANVAS_WIDTH - 1)
            red, green, blue = _mix_color(left, right, horizontal_ratio)
            rendered.append(character, style=f"rgb({red},{green},{blue})")
        if row_index < len(lines) - 1:
            rendered.append("\n")
    return rendered


class WelcomeMessage(Vertical):
    def __init__(
        self, workspace_path: str | Path, *, version: str | None = None
    ) -> None:
        super().__init__(classes="message welcome-message")
        self.workspace_path = Path(workspace_path).expanduser().resolve()
        self.version = version

    def compose(self) -> ComposeResult:
        yield Static(render_icarus_logo(), classes="welcome-logo")
        yield Label("Icarus", classes="welcome-title")
        yield Static(
            f"当前版本 {self.version or '未知'}   最新版本 未检查",
            markup=False,
            classes="welcome-version",
        )
        yield Static(
            f"Workspace  {self.workspace_path}",
            markup=False,
            classes="welcome-workspace",
        )
        yield Static(
            (
                "Enter submit · Shift+Enter/Ctrl+J newline · "
                "Ctrl+V system text/image paste · terminal Paste inserts text once\n"
                "Ctrl+C copy selection / actions · Ctrl+X cut selection (input only)\n"
                "Ctrl+Shift+S select chat · Shift+arrows extend · Esc cancel\n"
                "Ctrl+Shift+O open links · select message, then URL"
            ),
            markup=False,
            classes="welcome-help",
        )
