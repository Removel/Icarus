"""Clipboard resource tests use the production attachment owner."""

import asyncio
from pathlib import Path
import pytest

from apps.tui.src.platform.clipboard import ClipboardImage
from apps.tui.src.services.attachments import AttachmentsService
from apps.tui.src.domain.submission import DraftImage, PendingMessage


def test_paste_image_stores_private_temporary_file_and_cleanup(tmp_path):
    async def run():
        service = AttachmentsService(
            tmp_path,
            read_image=lambda: ClipboardImage(b"png", "image/png", "png"),
            read_text=lambda: None,
        )
        value = await service.read()
        image = value.image
        assert image.owned_temporary_file
        assert image.path.read_bytes() == b"png"
        assert image.path.stat().st_mode & 0o777 == 0o600
        directory = image.path.parent
        assert await service.close() == ()
        assert not directory.exists()

    asyncio.run(run())


def test_paste_returns_text_when_no_image(tmp_path):
    async def run():
        service = AttachmentsService(
            tmp_path, read_image=lambda: None, read_text=lambda: "中文\ntext"
        )
        result = await service.read()
        assert result.image is None and result.text == "中文\ntext"
        await service.close()

    asyncio.run(run())


def test_resources_reject_paths_outside_root(tmp_path):
    service = AttachmentsService(tmp_path)
    with pytest.raises(ValueError, match="controlled resource root"):
        service.resources(
            PendingMessage(
                "test", (DraftImage("image1", tmp_path.parent / "outside.png"),)
            )
        )


def test_paste_failure_propagates(tmp_path):
    def fail():
        raise OSError("clipboard unavailable")

    async def run():
        service = AttachmentsService(tmp_path, read_image=fail)
        with pytest.raises(OSError, match="unavailable"):
            await service.read()
        await service.close()

    asyncio.run(run())
