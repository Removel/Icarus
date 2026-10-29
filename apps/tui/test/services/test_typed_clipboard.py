"""Production clipboard read adopts one typed system snapshot."""

import asyncio

import pytest

from apps.tui.src.platform.clipboard import ClipboardContent, ClipboardImage, ClipboardUnavailableError
from apps.tui.src.services.attachments import AttachmentsService
from apps.tui.test.fixtures.clipboard_images import png


def test_single_typed_read_stages_file_snapshot_without_touching_source(tmp_path):
    async def run():
        source = tmp_path / "source.png"
        original = png()
        source.write_bytes(original)
        calls = []

        def read():
            calls.append(True)
            return ClipboardContent("file", files=(source,))

        service = AttachmentsService(tmp_path / "controlled", read_content=read)
        try:
            value = await service.read()
            assert len(calls) == 1
            assert value.image.owned_temporary_file
            assert value.image.path != source
            assert value.image.path.read_bytes() == original
            source.unlink()
            assert value.image.path.read_bytes() == original
        finally:
            await service.close()
        assert not value.image.path.exists()
    asyncio.run(run())


def test_typed_text_does_not_read_image_then_reread_clipboard(tmp_path):
    async def run():
        calls = []
        def read():
            calls.append(True)
            return ClipboardContent("text", text="空白 \n中文😀\n")
        service = AttachmentsService(tmp_path, read_content=read)
        value = await service.read()
        assert value.text == "空白 \n中文😀\n"
        assert value.image is None
        assert len(calls) == 1
        await service.close()
    asyncio.run(run())


def test_typed_corrupt_image_does_not_leave_temp_resource(tmp_path):
    async def run():
        service = AttachmentsService(tmp_path, read_content=lambda: ClipboardContent(
            "image", image=ClipboardImage(b"not png", "image/png", "png")))
        with pytest.raises(ClipboardUnavailableError):
            await service.read()
        await service.close()
        assert not list(tmp_path.iterdir())
    asyncio.run(run())
