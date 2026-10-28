"""Cancellation and sequencing contracts for desktop system calls."""

import asyncio
import threading

import pytest

from apps.tui.src.platform.clipboard import ClipboardUnavailableError
from apps.tui.src.services.desktop import DesktopService


def test_copy_does_not_block_loop_and_preserves_request_order():
    async def run():
        entered = threading.Event()
        release = threading.Event()
        written = []

        def write(text):
            if text == "first":
                entered.set()
                assert release.wait(3)
            written.append(text)

        service = DesktopService(write_text=write)
        first = asyncio.create_task(service.copy_text("first"))
        assert await asyncio.to_thread(entered.wait, 2)
        second = asyncio.create_task(service.copy_text("second"))
        await asyncio.sleep(0.03)
        try:
            assert written == []
        finally:
            release.set()
            await asyncio.gather(first, second)
        assert written == ["first", "second"]
        await service.close()
    asyncio.run(run())


def test_cancelled_copy_is_drained_before_next_write_and_close():
    async def run():
        entered = threading.Event()
        release = threading.Event()
        written = []

        def write(text):
            if text == "first":
                entered.set()
                assert release.wait(3)
            written.append(text)

        service = DesktopService(write_text=write)
        first = asyncio.create_task(service.copy_text("first"))
        assert await asyncio.to_thread(entered.wait, 2)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        second = asyncio.create_task(service.copy_text("second"))
        await asyncio.sleep(0.03)
        closing = asyncio.create_task(service.close())
        await asyncio.sleep(0.03)
        try:
            assert not closing.done()
            assert written == []
        finally:
            release.set()
            await asyncio.gather(second, closing)
        assert written == ["first", "second"]
        with pytest.raises(RuntimeError, match="clos"):
            await service.copy_text("after close")
    asyncio.run(run())


def test_failed_write_does_not_poison_later_copy():
    async def run():
        written = []

        def write(text):
            if text == "first":
                raise ClipboardUnavailableError("access: unavailable")
            written.append(text)

        service = DesktopService(write_text=write)
        with pytest.raises(ClipboardUnavailableError):
            await service.copy_text("first")
        await service.copy_text("second")
        assert written == ["second"]
        await service.close()
    asyncio.run(run())
