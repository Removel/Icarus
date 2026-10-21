"""Sync tool adapters submit to the existing host event loop, never a new loop."""

import asyncio
from collections.abc import Awaitable, Callable
from threading import get_ident
from typing import TypeVar

T = TypeVar("T")


class RuntimeLoopRunner:
    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread_id: int | None = None

    def bind(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._thread_id = get_ident()

    def run(self, operation: Callable[[], Awaitable[T]]) -> T:
        loop = self._loop
        if loop is None or loop.is_closed() or not loop.is_running():
            raise RuntimeError("Runtime loop is unavailable")
        if self._thread_id == get_ident():
            raise RuntimeError("Use ainvoke from the runtime event loop")
        async def invoke():
            return await operation()
        future = asyncio.run_coroutine_threadsafe(invoke(), loop)
        try:
            return future.result(timeout=35)
        except BaseException:
            future.cancel()
            raise

    def close(self) -> None:
        self._loop = None
        self._thread_id = None
