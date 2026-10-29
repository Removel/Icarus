"""Own clipboard reads, temporary attachments and Gateway resource references."""

from __future__ import annotations

import asyncio
import tempfile
from dataclasses import dataclass
from pathlib import Path

from apps.tui.src.domain.submission import DraftImage, PendingMessage
from apps.tui.src.platform.clipboard import (
    ClipboardUnavailableError,
    read_clipboard_content,
    read_clipboard_image,
    read_clipboard_text,
    read_image_file,
    validate_clipboard_image,
)
from packages.gateway_protocol import ResourceRefModel


@dataclass(frozen=True)
class ClipboardRead:
    image: DraftImage | None = None
    text: str | None = None


class AttachmentsService:
    def __init__(
        self,
        resource_root: Path | None,
        *,
        read_image=None,
        read_text=None,
        read_content=None,
    ):
        self.root = Path(resource_root).resolve() if resource_root else None
        self._read_image = read_image
        self._read_text = read_text
        self._read_content = read_content or (
            read_clipboard_content if read_image is None and read_text is None else None
        )
        self._directory = None
        self._reads: set[asyncio.Task] = set()
        self._closing = False

    def _read(self) -> ClipboardRead:
        if self._read_content is not None:
            content = self._read_content()
            if content.kind == "text":
                return ClipboardRead(text=content.text)
            if content.kind == "file":
                if len(content.files) != 1:
                    raise ClipboardUnavailableError("multiple or empty file clipboard")
                image = read_image_file(content.files[0])
            elif content.image is not None:
                image = validate_clipboard_image(content.image)
            else:
                raise ClipboardUnavailableError("unsupported: missing clipboard image")
        else:
            # Preserve explicit legacy reader injection for existing callers.
            image = (self._read_image or read_clipboard_image)()
            if image is None:
                return ClipboardRead(text=(self._read_text or read_clipboard_text)())
        if self._closing:
            return ClipboardRead()
        if self._directory is None:
            if self.root:
                self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
            self._directory = tempfile.TemporaryDirectory(
                prefix="icarus-tui-clipboard-", dir=self.root
            )
        with tempfile.NamedTemporaryFile(
            mode="wb",
            suffix=f".{image.extension}",
            dir=self._directory.name,
            delete=False,
        ) as file:
            file.write(image.data)
            path = Path(file.name)
        path.chmod(0o600)
        return ClipboardRead(image=DraftImage("clipboard", path, True))

    async def read(self) -> ClipboardRead:
        if self._closing:
            return ClipboardRead()
        task = asyncio.create_task(asyncio.to_thread(self._read))
        self._reads.add(task)
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            # The OS call keeps running. Retain it so close drains it before cleanup.
            raise
        finally:
            if task.done():
                self._reads.discard(task)

    def resources(self, submission: PendingMessage) -> tuple[ResourceRefModel, ...]:
        def identity(image):
            if self.root is None:
                return image.path.name
            try:
                return image.path.resolve().relative_to(self.root).as_posix()
            except ValueError as error:
                raise ValueError(
                    "Image is outside the controlled resource root"
                ) from error

        return tuple(
            ResourceRefModel(resource_id=identity(image), media_type=None)
            for image in submission.images
        )

    def release(self, images: tuple[DraftImage, ...]) -> None:
        for image in images:
            if image.owned_temporary_file:
                try:
                    image.path.unlink(missing_ok=True)
                except OSError:
                    pass

    async def close(self) -> tuple[str, ...]:
        self._closing = True
        if self._reads:
            await asyncio.gather(*self._reads, return_exceptions=True)
            self._reads.clear()
        if self._directory:
            directory, self._directory = self._directory, None
            try:
                directory.cleanup()
            except OSError as error:
                return (f"clipboard cleanup: {error}",)
        return ()
