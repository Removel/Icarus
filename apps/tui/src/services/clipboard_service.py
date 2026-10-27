"""Clipboard image storage and resource lifecycle without Textual."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
import tempfile

from packages.gateway_protocol import ResourceRefModel
from apps.tui.src.platform.clipboard import ClipboardImage, read_clipboard_image
from apps.tui.src.domain.submission import (
    DraftImage,
    PendingMessage,
    delete_submission_images,
    submission_resources,
)


class ClipboardService:
    """Own one temporary directory for pasted images."""

    def __init__(
        self,
        resource_root: Path | None,
        *,
        read_image: Callable[[], ClipboardImage | None] = read_clipboard_image,
    ) -> None:
        self._resource_root = (
            Path(resource_root).expanduser().resolve()
            if resource_root is not None
            else None
        )
        self._read_image = read_image
        self._temporary_directory: tempfile.TemporaryDirectory[str] | None = None

    def paste_image(self) -> DraftImage | None:
        image = self._read_image()
        if image is None:
            return None
        directory = self._ensure_directory()
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix="image-",
            suffix=f".{image.extension}",
            dir=directory.name,
            delete=False,
        ) as file:
            file.write(image.data)
            path = Path(file.name)
        path.chmod(0o600)
        return DraftImage(
            "clipboard",
            path,
            owned_temporary_file=True,
        )

    def resources(
        self, submission: PendingMessage
    ) -> tuple[ResourceRefModel, ...]:
        return submission_resources(submission, self._resource_root)

    def discard(self, submission: PendingMessage) -> None:
        delete_submission_images(submission)

    def cleanup(self) -> list[str]:
        directory = self._temporary_directory
        self._temporary_directory = None
        if directory is None:
            return []
        try:
            directory.cleanup()
        except Exception as error:
            return [
                f"clipboard files: {type(error).__name__}: {error}"
            ]
        return []

    def _ensure_directory(self) -> tempfile.TemporaryDirectory[str]:
        directory = self._temporary_directory
        if directory is None:
            root = self._resource_root
            if root is not None:
                root.mkdir(parents=True, exist_ok=True, mode=0o700)
            directory = tempfile.TemporaryDirectory(
                prefix="icarus-tui-clipboard-",
                dir=str(root) if root is not None else None,
            )
            self._temporary_directory = directory
        return directory
