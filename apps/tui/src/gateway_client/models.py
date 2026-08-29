"""Small TUI-facing values returned by the Gateway client."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from packages.gateway_protocol import RuntimeUpdateModel


@dataclass(frozen=True)
class SubmitAccepted:
    task_id: str
    queue_position: int


@dataclass(frozen=True)
class TaskOperationResult:
    task_id: str | None
    status: str
    run_id: str | None = None


class UpdateSubscription:
    def __init__(self) -> None:
        self._queue: asyncio.Queue[RuntimeUpdateModel | BaseException | None] = (
            asyncio.Queue()
        )
        self._closed = False
        self._read_lock = asyncio.Lock()

    async def next_update(self) -> RuntimeUpdateModel:
        async with self._read_lock:
            if self._closed and self._queue.empty():
                raise RuntimeError("Gateway update subscription is closed")
            item = await self._queue.get()
            if item is None:
                raise RuntimeError("Gateway update subscription is closed")
            if isinstance(item, BaseException):
                raise item
            return item

    def publish(self, update: RuntimeUpdateModel) -> None:
        if not self._closed:
            self._queue.put_nowait(update)

    def close(self, error: BaseException | None = None) -> None:
        if self._closed:
            return
        self._closed = True
        while True:
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        self._queue.put_nowait(error)
