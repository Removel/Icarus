"""Focused message widgets rendered inside the conversation view."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Label, Markdown, Static

from apps.tui.src.widgets.chat.links import LinkText, url_ranges
from apps.tui.src.widgets.chat.local_scroll import LocalScroll
from apps.tui.src.widgets.chat.markdown import StreamingMarkdown


class UserMessage(Vertical):
    def __init__(self, text: str) -> None:
        super().__init__(classes="message user-message")
        self.message_text = text

    def link_targets(self):
        return [url for _, _, url in url_ranges(self.message_text)]

    def compose(self) -> ComposeResult:
        yield Label("❯ You", classes="message-label")
        with LocalScroll(classes="message-body-scroll"):
            yield LinkText(self.message_text, markup=False, classes="message-content")


class _StreamingMessage(Vertical):
    def __init__(self, *, classes: str) -> None:
        super().__init__(classes=classes)
        self._markdown_parts: list[str] = []
        self._segment_finished = False
        self._markdown_stream = None

    @property
    def markdown_text(self) -> str:
        return "".join(self._markdown_parts)

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


class AssistantMessage(_StreamingMessage):
    def __init__(self) -> None:
        super().__init__(classes="message assistant-message")

    def compose(self) -> ComposeResult:
        yield Label("✦ Icarus", classes="message-label")
        yield StreamingMarkdown("", classes="assistant-markdown")


class AssistantProgressBlock(_StreamingMessage):
    def __init__(self, step: int) -> None:
        super().__init__(classes="assistant-progress")
        self.step = step

    def compose(self) -> ComposeResult:
        yield Label(f"Progress · step {self.step}", classes="message-label")
        yield StreamingMarkdown("", classes="assistant-markdown")


class UserCorrectionBlock(Vertical):
    def __init__(self, text: str, applied_before_step: int) -> None:
        super().__init__(classes="message user-message user-correction-block")
        self.correction_text = text
        self.applied_before_step = applied_before_step

    def compose(self) -> ComposeResult:
        yield Static(
            f"❯ Added by you before step {self.applied_before_step}",
            markup=False,
            classes="correction-label",
        )
        yield Static(
            self.correction_text,
            markup=False,
            classes="correction-content",
        )


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
