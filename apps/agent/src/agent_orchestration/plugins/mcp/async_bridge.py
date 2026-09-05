"""Own one asyncio loop for the Session-scoped FastMCP clients."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from concurrent.futures import Future
import threading
from typing import TypeVar


T = TypeVar("T")


class MCPAsyncBridge:
    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._lifecycle_lock = threading.Lock()

    @property
    def is_running(self) -> bool:
        return self._loop is not None and self._thread is not None

    def start(self) -> None:
        with self._lifecycle_lock:
            if self._thread is not None:
                return
            self._thread = threading.Thread(
                target=self._run_loop,
                name="icarus-mcp-client",
                daemon=True,
            )
            self._thread.start()
            if not self._ready.wait(timeout=5):
                self._thread = None
                raise RuntimeError("MCP bridge event loop failed to start")

    def submit(self, operation: Callable[[], Awaitable[T]]) -> Future[T]:
        loop = self._require_loop()
        if threading.current_thread() is self._thread:
            raise RuntimeError("MCP bridge cannot synchronously submit to itself")
        return asyncio.run_coroutine_threadsafe(operation(), loop)

    def run(self, operation: Callable[[], Awaitable[T]]) -> T:
        return self.submit(operation).result()

    async def arun(self, operation: Callable[[], Awaitable[T]]) -> T:
        future = self.submit(operation)
        try:
            return await asyncio.wrap_future(future)
        except asyncio.CancelledError:
            future.cancel()
            raise

    def stop(self) -> None:
        with self._lifecycle_lock:
            loop = self._loop
            thread = self._thread
            if loop is None or thread is None:
                return
            loop.call_soon_threadsafe(loop.stop)
            thread.join()
            self._loop = None
            self._thread = None
            self._ready.clear()

    def _require_loop(self) -> asyncio.AbstractEventLoop:
        if self._loop is None or self._thread is None:
            raise RuntimeError("MCP bridge is not running")
        return self._loop

    def _run_loop(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        self._ready.set()
        try:
            loop.run_forever()
        finally:
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(
                    asyncio.gather(*pending, return_exceptions=True)
                )
            loop.run_until_complete(loop.shutdown_asyncgens())
            loop.close()
