"""Focused message widgets rendered inside the conversation view."""

from __future__ import annotations

from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Label, Markdown, Static


ICARUS_LOGO = """▄▄▄▄▄ ▄▄▄▄▄▄▄▄▄▄    ▄▄▄▄▄    ▄▄▄▄▄▄▄▄▄   ▄▄▄▄▄ ▄▄▄▄▄ ▄▄▄▄▄▄▄▄▄▄▄
█▒░░█ █▒░░░░░░░█  ▄▀░░░░░ ▄  █▒░░░░░░░█▄ █▒░░█ █░░░█ █▒░░░░░▒░░█
█▓▒▒█ █▓▒▒█▀▀▀▀▀ █▓▒▒▄▀▄▓▒▒█ █▓▒▒█▀▄▒▒▒█ █▓▒▒█ █▒▒▒█ █▓▒▒█▀▀▀▀▀▀
██▓▓█ ██▓▓█      ██▓▓▄▄██▓▓█ ██▓▓▄▄▓▓▓▄▀ ██▓▓█ █▓▓▓█ ██▓▓▄███▓▓█
█▄▄▄█ █▄▄▄█      █▄▄▄▄▄▄▄▄▄█ █▄▄▄▄▄▄▄▄█  █▄▄▄█ █▄▄▄█ ▀▄▄▄▄▄▄▄▄▄█
█▒░░█ █▒░░▀▄▄▄▄▄ █▒░░█ █▒░░█ █▒░░█ █▒░░█ █▓░░▀▄▄▒░░█ ▄▄▄▄▄▄█▒░░█
█░░░█ █░░░░░░░░█ █░░░█ █░░░█ █░░░█ █░░░█ █░░░░░░░░░█ █░░░░░░░░░█
▀▀▀▀▀ ▀▀▀▀▀▀▀▀▀▀ ▀▀▀▀▀ ▀▀▀▀▀ ▀▀▀▀▀ ▀▀▀▀▀ ▀▀▀▀▀▀▀▀▀▀▀ ▀▀▀▀▀▀▀▀▀▀▀"""


class WelcomeMessage(Vertical):
    def __init__(self, workspace_path: str | Path) -> None:
        super().__init__(classes="message welcome-message")
        self.workspace_path = Path(workspace_path).expanduser().resolve()

    def compose(self) -> ComposeResult:
        yield Static(ICARUS_LOGO, markup=False, classes="welcome-logo")
        yield Label("Icarus", classes="welcome-title")
        yield Static(
            f"Workspace  {self.workspace_path}",
            markup=False,
            classes="welcome-workspace",
        )
        yield Static(
            (
                "Enter submit · Shift+Enter/Ctrl+J newline · "
                "Ctrl+C clear/restore/cancel/exit"
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

    @property
    def markdown_text(self) -> str:
        return "".join(self._markdown_parts)

    def compose(self) -> ComposeResult:
        yield Label("Icarus", classes="message-label")
        yield Markdown("", classes="assistant-markdown")

    async def append_delta(self, text: str) -> None:
        if self._segment_finished:
            raise RuntimeError("Assistant message is already closed")
        if not text:
            return
        self._markdown_parts.append(text)
        await self.query_one(Markdown).update(self.markdown_text)

    async def finish(self) -> None:
        if self._segment_finished:
            return
        self._segment_finished = True


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
