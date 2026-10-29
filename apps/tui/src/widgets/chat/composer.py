"""Persistent multiline input widget for the Icarus TUI."""

from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from textual import events
from textual.message import Message
from textual.widgets import TextArea

from apps.tui.src.domain.submission import (
    DraftImage,
    PendingMessage,
    referenced_images,
)


@dataclass(frozen=True)
class CutSnapshot:
    text: str
    start: tuple[int, int]
    end: tuple[int, int]
    text_version: int
    draft_token: int


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
    class ImagesReleased(Message):
        images: tuple[DraftImage, ...]

    @dataclass
    class CopyTextRequested(Message):
        text: str
        snapshot: CutSnapshot | None = None

    @dataclass
    class ImagePasteRequested(Message):
        pass

    def __init__(self, *, id: str | None = None) -> None:
        self._text_version = 0
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
        self.draft_token = 0
        self.key_handler = None

    def load_text(self, text):
        self._text_version += 1
        return super().load_text(text)

    def undo(self):
        self._text_version += 1
        return super().undo()

    def redo(self):
        self._text_version += 1
        return super().redo()

    def edit(self, edit):
        result = super().edit(edit)
        self._text_version += 1
        return result

    def get_selection_text(self) -> str | None:
        return self.selected_text if self.selection.start != self.selection.end else None

    def begin_cut(self) -> CutSnapshot | None:
        text = self.get_selection_text()
        if self.read_only or text is None:
            return None
        start, end = sorted(self.selection)
        return CutSnapshot(text, start, end, self._text_version, self.draft_token)

    def apply_cut(self, snapshot: CutSnapshot) -> bool:
        if (self.read_only or snapshot.draft_token != self.draft_token
                or snapshot.text_version != self._text_version
                or tuple(sorted(self.selection)) != (snapshot.start, snapshot.end)
                or self.get_selection_text() != snapshot.text):
            return False
        result = self.replace("", snapshot.start, snapshot.end, maintain_selection_offset=False)
        self.move_cursor(result.end_location)
        return True

    def action_copy_selection(self) -> None:
        text = self.get_selection_text()
        if text is not None:
            self.post_message(self.CopyTextRequested(text))

    def action_copy(self) -> None:
        self.action_copy_selection()

    def action_cut(self) -> None:
        snapshot = self.begin_cut()
        if snapshot is not None:
            self.post_message(self.CopyTextRequested(snapshot.text, snapshot))

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

        if self.key_handler is not None and self.key_handler(event):
            event.stop()
            event.prevent_default()
            return
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

    async def _on_mouse_down(self, event: events.MouseDown) -> None:
        event.prevent_default()
        if event.button == 3:
            event.stop()
            self.action_paste()
            return
        await super()._on_mouse_down(event)

    async def _on_paste(self, event: events.Paste) -> None:
        # Handle once, without dispatching the base handler a second time.
        event.stop()
        event.prevent_default()
        await super()._on_paste(event)

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
        unused = tuple(image for image in self._images.values() if image not in images)
        if unused:
            self.post_message(self.ImagesReleased(unused).set_sender(self))
        self.clear_draft(delete_images=False)
        self.post_message(self.Submitted(submission).set_sender(self))
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
        self.draft_token += 1
        if delete_images and self._images:
            self.post_message(
                self.ImagesReleased(tuple(self._images.values())).set_sender(self)
            )
        self.clear()
        self._images.clear()
        self._next_image_number = 1
        self._submission_id = None
        self.move_cursor((0, 0))

    def restore_draft(self, draft: str | PendingMessage) -> None:
        self.draft_token += 1
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
