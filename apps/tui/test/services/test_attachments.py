import asyncio
import threading
from pathlib import Path

from apps.tui.src.platform.clipboard import ClipboardImage


def test_cancelled_clipboard_read_is_drained_before_close(tmp_path):
    from apps.tui.src.services.attachments import AttachmentsService

    async def run():
        entered, release = threading.Event(), threading.Event()

        def reader():
            entered.set()
            release.wait(3)
            return ClipboardImage(b"png", "image/png", "png")

        service = AttachmentsService(
            tmp_path, read_image=reader, read_text=lambda: None
        )
        task = asyncio.create_task(service.read())
        await asyncio.to_thread(entered.wait, 2)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        close = asyncio.create_task(service.close())
        release.set()
        assert await close == ()
        assert list(tmp_path.iterdir()) == []

    asyncio.run(run())


def test_resources_preserve_order_and_only_release_owned(tmp_path):
    from apps.tui.src.services.attachments import AttachmentsService
    from apps.tui.src.domain.submission import DraftImage, PendingMessage

    service = AttachmentsService(
        tmp_path, read_image=lambda: None, read_text=lambda: None
    )
    owned, shared = tmp_path / "owned.png", tmp_path / "shared.png"
    owned.write_bytes(b"a")
    shared.write_bytes(b"b")
    images = (DraftImage("image2", shared), DraftImage("image1", owned, True))
    assert [
        r.resource_id for r in service.resources(PendingMessage("test", images))
    ] == ["shared.png", "owned.png"]
    service.release(images)
    assert not owned.exists()
    assert shared.exists()
