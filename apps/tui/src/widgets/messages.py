"""Focused message widgets rendered inside the conversation view."""

from __future__ import annotations

from pathlib import Path

from rich.text import Text
from textual.app import ComposeResult
from textual.await_complete import AwaitComplete
from textual.containers import Vertical
from textual.message import Message
from textual.widgets import Label, Markdown, Static


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
        round(left + (right - left) * ratio)
        for left, right in zip(start, end)
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


class StreamingMarkdown(Markdown):
    """Keep stale blocks out of Textual's mouse-selection path."""

    class ContentAppended(Message):
        """The streamed fragment has been rendered into Markdown blocks."""

    def update(self, markdown: str):
        for child in self.walk_children():
            child.ALLOW_SELECT = False
        return super().update(markdown)

    def append(self, markdown: str) -> AwaitComplete:
        previous_tail = self.children[-1] if self.children else None
        if previous_tail is not None:
            previous_tail.ALLOW_SELECT = False
        append = super().append(markdown)

        async def await_append() -> None:
            try:
                await append
            finally:
                if previous_tail is not None and previous_tail.parent is not None:
                    previous_tail.ALLOW_SELECT = True
            self.post_message(self.ContentAppended())

        return AwaitComplete(await_append())


class WelcomeMessage(Vertical):
    def __init__(self, workspace_path: str | Path) -> None:
        super().__init__(classes="message welcome-message")
        self.workspace_path = Path(workspace_path).expanduser().resolve()

    def compose(self) -> ComposeResult:
        yield Static(render_icarus_logo(), classes="welcome-logo")
        yield Label("Icarus", classes="welcome-title")
        yield Static(
            f"Workspace  {self.workspace_path}",
            markup=False,
            classes="welcome-workspace",
        )
        yield Static(
            (
                "Enter submit · Shift+Enter/Ctrl+J newline · "
                "Ctrl+V image · Ctrl+C actions"
            ),
            markup=False,
            classes="welcome-help",
        )


class UserMessage(Vertical):
    def __init__(self, text: str) -> None:
        super().__init__(classes="message user-message")
        self.message_text = text

    def compose(self) -> ComposeResult:
        yield Label("You", classes="message-label")
        yield Static(self.message_text, markup=False, classes="message-content")


class AssistantMessage(Vertical):
    def __init__(self) -> None:
        super().__init__(classes="message assistant-message")
        self._markdown_parts: list[str] = []
        self._segment_finished = False
        self._markdown_stream = None

    @property
    def markdown_text(self) -> str:
        return "".join(self._markdown_parts)

    def compose(self) -> ComposeResult:
        yield Label("Icarus", classes="message-label")
        yield StreamingMarkdown("", classes="assistant-markdown")

    async def append_delta(self, text: str) -> None:
        if self._segment_finished:
            raise RuntimeError("Assistant message is already closed")
        if not text:
            return
        self._markdown_parts.append(text)
        if self._markdown_stream is None:
            markdown = self.query_one(StreamingMarkdown)
            self._markdown_stream = Markdown.get_stream(markdown)
        await self._markdown_stream.write(text)

    async def complete_text(self, text: str) -> None:
        """Reconcile streamed content with one complete assistant message."""

        if self._segment_finished:
            raise RuntimeError("Assistant message is already closed")
        current = self.markdown_text
        if current == text:
            return
        if text.startswith(current):
            await self.append_delta(text[len(current) :])
            return
        stream = self._markdown_stream
        self._markdown_stream = None
        if stream is not None:
            await stream.stop()
        self._markdown_parts = [text]
        await self.query_one(StreamingMarkdown).update(text)

    async def finish(self) -> None:
        if self._segment_finished:
            return
        self._segment_finished = True
        stream = self._markdown_stream
        self._markdown_stream = None
        if stream is not None:
            await stream.stop()

    async def on_unmount(self) -> None:
        await self.finish()


class ToolMessage(Vertical):
    def __init__(
        self,
        *,
        call_id: str,
        tool_name: str,
        arguments_json: str = "{}",
    ) -> None:
        super().__init__(classes="message tool-message is-running")
        self.call_id = call_id
        self.tool_name = tool_name
        self.arguments_json = arguments_json
        self.success: bool | None = None
        self.error: str | None = None

    def compose(self) -> ComposeResult:
        yield Static(
            f"◆ {self.tool_name} {self.arguments_json}",
            markup=False,
            classes="tool-summary",
        )
        yield Static(
            "running", markup=False, classes="tool-state"
        )

    def complete(self, *, success: bool, error: str | None = None) -> None:
        self.success = success
        self.error = error if not success else None
        self.remove_class("is-running")
        self.set_class(success, "is-success")
        self.set_class(not success, "is-failed")
        if self.is_mounted:
            state = "completed" if success else "failed"
            if self.error:
                state = f"{state}: {self.error}"
            self.query_one(".tool-state", Static).update(state)

    def interrupt(self) -> None:
        if self.success is not None:
            return
        self.remove_class("is-running")
        self.add_class("is-interrupted")
        if self.is_mounted:
            self.query_one(".tool-state", Static).update("interrupted")


class ErrorMessage(Vertical):
    def __init__(self, error_type: str, message: str) -> None:
        super().__init__(classes="message error-message")
        self.error_type = error_type
        self.error_message = message

    def compose(self) -> ComposeResult:
        yield Label("Error", classes="message-label")
        yield Static(
            f"{self.error_type}: {self.error_message}",
            markup=False,
            classes="message-content",
        )


class TurnStatusMessage(Static):
    def __init__(self, status: str) -> None:
        super().__init__(
            f"Task {status}",
            markup=False,
            classes=f"message turn-status turn-{status}",
        )
