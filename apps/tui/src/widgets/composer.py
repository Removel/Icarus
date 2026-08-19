"""Persistent multiline input widget for the Icarus TUI."""

from dataclasses import dataclass

from textual import events
from textual.message import Message
from textual.widgets import TextArea


class PersistentComposer(TextArea):
    """A TextArea where Enter submits and modified Enter inserts a line."""

    MAX_VISIBLE_LINES = 8

    @dataclass
    class Submitted(Message):
        text: str

    def __init__(self, *, id: str | None = None) -> None:
        super().__init__(
            soft_wrap=True,
            tab_behavior="indent",
            show_line_numbers=False,
            compact=True,
            highlight_cursor_line=False,
            placeholder="Ask Icarus…",
            id=id,
        )
        self.styles.height = 1

    def on_mount(self) -> None:
        self._sync_height()

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        if event.text_area is self:
            self._sync_height()

    def _sync_height(self) -> None:
        """Grow with logical lines and scroll after the configured cap."""

        self.styles.height = min(
            max(1, self.document.line_count),
            self.MAX_VISIBLE_LINES,
        )

    async def _on_key(self, event: events.Key) -> None:
        """Override TextArea's built-in Enter-to-newline behavior."""

        if event.key == "enter":
            event.stop()
            event.prevent_default()
            self.submit()
            return
        if event.key in {"shift+enter", "ctrl+j", "newline"}:
            event.stop()
            event.prevent_default()
            self.insert_newline()
            return
        await super()._on_key(event)

    def submit(self) -> bool:
        value = self.text
        if not value.strip():
            return False
        self.clear()
        self.post_message(self.Submitted(value))
        return True

    def insert_newline(self) -> None:
        start, end = self.selection
        result = self.replace(
            "\n",
            start,
            end,
            maintain_selection_offset=False,
        )
        self.move_cursor(result.end_location)

    def clear_draft(self) -> None:
        self.clear()
        self.move_cursor((0, 0))

    def restore_draft(self, text: str) -> None:
        self.load_text(text)
        self.move_cursor(self.document.end)
