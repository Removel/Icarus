"""Track actual tool completion independently of callers waiting for a result."""

import asyncio
from concurrent.futures import Future
from threading import Lock


class ToolExecutionTracker:
    def __init__(self):
        self._pending: set[asyncio.Future | Future] = set()
        self._waiters: list[tuple[asyncio.AbstractEventLoop, asyncio.Future]] = []
        self._lock = Lock()

    @property
    def pending_count(self) -> int:
        with self._lock:
            return sum(not future.done() for future in self._pending)

    def track(self, future) -> None:
        with self._lock:
            self._pending.add(future)
        future.add_done_callback(self._completed)

    def _completed(self, future) -> None:
        if not future.cancelled():
            future.exception()
        with self._lock:
            self._pending.discard(future)
            if any(not item.done() for item in self._pending):
                return
            waiters, self._waiters = self._waiters, []
        for loop, waiter in waiters:
            if not loop.is_closed():
                loop.call_soon_threadsafe(_wake, waiter)

    async def drain(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            with self._lock:
                if not any(not future.done() for future in self._pending):
                    return
                waiter = loop.create_future()
                entry = (loop, waiter)
                self._waiters.append(entry)
            try:
                await waiter
            finally:
                with self._lock:
                    if entry in self._waiters:
                        self._waiters.remove(entry)


def _wake(waiter):
    if not waiter.done():
        waiter.set_result(None)
