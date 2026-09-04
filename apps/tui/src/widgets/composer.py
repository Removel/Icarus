"""Persistent multiline input widget for the Icarus TUI."""

from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from textual import events
from textual.message import Message
from textual.widgets import TextArea

from apps.tui.src.submission import (
    DraftImage,
    PendingMessage,
    referenced_images,
)


class PersistentComposer(TextArea):
    """A TextArea where Enter submits and modified Enter inserts a line."""

    MAX_VISIBLE_LINES = 8

    @dataclass
    class Submitted(Message):
        submission: PendingMessage

        @property
        def text(self) -> str:
            return self.submission.text

        @property
        def images(self) -> tuple[DraftImage, ...]:
            return self.submission.images

    @dataclass
    class ImagePasteRequested(Message):
        pass

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
        self._images: dict[str, DraftImage] = {}
        self._next_image_number = 1
        self._submission_id: str | None = None

    def on_mount(self) -> None:
        self._sync_height()

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        if event.text_area is self:
            self._sync_height()

    def _on_resize(self) -> None:
        super()._on_resize()
        self.call_after_refresh(self._sync_height)

    def _sync_height(self) -> None:
        """Grow with visual wrapped lines and scroll after the cap."""

        self.styles.height = min(
            max(1, self.wrapped_document.height),
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
        images = referenced_images(value, tuple(self._images.values()))
        if not value.strip() and not images:
            return False
        submission = PendingMessage(
            value,
            images,
            submission_id=(self._submission_id or uuid4().hex),
        )
        self.clear_draft(delete_images=False)
        self.post_message(self.Submitted(submission))
        return True

    @property
    def has_draft(self) -> bool:
        return bool(self.text or self._images)

    @property
    def images(self) -> tuple[DraftImage, ...]:
        return tuple(self._images.values())

    def action_paste(self) -> None:
        if not self.read_only:
            self.post_message(self.ImagePasteRequested())

    def paste_text_from_clipboard(self) -> None:
        super().action_paste()

    def attach_image(
        self, path: str | Path, *, owned_temporary_file: bool = False
    ) -> DraftImage:
        reference = f"image{self._next_image_number}"
        self._next_image_number += 1
        image = DraftImage(
            reference, Path(path), owned_temporary_file=owned_temporary_file
        )
        self._images[reference] = image
        start, end = self.selection
        result = self.replace(
            image.marker,
            start,
            end,
            maintain_selection_offset=False,
        )
        self.move_cursor(result.end_location)
        self.focus()
        return image

    def insert_newline(self) -> None:
        start, end = self.selection
        result = self.replace(
            "\n",
            start,
            end,
            maintain_selection_offset=False,
        )
        self.move_cursor(result.end_location)

    def clear_draft(self, *, delete_images: bool = True) -> None:
        if delete_images:
            for image in self._images.values():
                if not image.owned_temporary_file:
                    continue
                try:
                    image.path.unlink(missing_ok=True)
                except OSError:
                    pass
        self.clear()
        self._images.clear()
        self._next_image_number = 1
        self._submission_id = None
        self.move_cursor((0, 0))

    def restore_draft(self, draft: str | PendingMessage) -> None:
        submission = (
            draft if isinstance(draft, PendingMessage) else PendingMessage(draft)
        )
        self.load_text(submission.text)
        self._images = {image.reference: image for image in submission.images}
        self._submission_id = submission.submission_id
        numbers = [
            int(image.reference.removeprefix("image"))
            for image in submission.images
            if image.reference.removeprefix("image").isdigit()
        ]
        self._next_image_number = max(numbers, default=0) + 1
        self.move_cursor(self.document.end)
