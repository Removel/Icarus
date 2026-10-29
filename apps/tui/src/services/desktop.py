"""Desktop I/O service: system clipboard writes and validated URL opening."""

from __future__ import annotations

import asyncio
from typing import Callable

from apps.tui.src.platform.clipboard import write_clipboard_text

from apps.tui.src.platform.opener import validate_http_url


class DesktopService:
    """Runs clipboard writes and URL opens off the Textual message loop."""

    def __init__(
        self,
        *,
        write_text: Callable[[str], None] = write_clipboard_text,
        open_url: Callable[[str], None] | None = None,
    ):
        self._write_text = write_text
        self._open_url = open_url
        self._write_lock = asyncio.Lock()
        self._operations: set[asyncio.Task] = set()
        self._closing = False

    async def _run(self, operation) -> None:
        if self._closing:
            raise RuntimeError("Desktop service is closing")
        task = asyncio.create_task(operation())
        self._operations.add(task)
        task.add_done_callback(self._finished)
        # Cancelling a Textual Worker must not cancel the real system write or
        # release serialization while its thread is still touching the clipboard.
        await asyncio.shield(task)

    def _finished(self, task) -> None:
        self._operations.discard(task)
        if not task.cancelled():
            task.exception()  # Retrieve exceptions even if the caller was cancelled.

    async def copy_text(self, text: str) -> None:
        """Serialize real writes, including operations whose callers cancel."""
        async def write():
            async with self._write_lock:
                await asyncio.to_thread(self._write_text, text)
        await self._run(write)

    async def open_link(self, url: str) -> None:
        validated = validate_http_url(url)
        if self._open_url is None:
            raise ValueError("no URL opener is wired")
        await self._run(lambda: asyncio.to_thread(self._open_url, validated))

    async def close(self) -> None:
        self._closing = True
        while self._operations:
            await asyncio.shield(asyncio.gather(*self._operations, return_exceptions=True))
