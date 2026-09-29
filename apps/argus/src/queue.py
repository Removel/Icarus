"""Bounded inbound queue for external observations."""

import asyncio

from .entity.observation import Observation


class InboundQueue:
    def __init__(self, capacity: int) -> None:
        self._items: asyncio.Queue[Observation] = asyncio.Queue(maxsize=capacity)

    def put_nowait(self, item: Observation) -> None:
        self._items.put_nowait(item)

    async def get(self) -> Observation:
        return await self._items.get()

    def task_done(self) -> None:
        self._items.task_done()

    async def join(self) -> None:
        await self._items.join()
