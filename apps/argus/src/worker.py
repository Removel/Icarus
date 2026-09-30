"""Lifecycle of the Argus inbound worker."""

import asyncio
from collections.abc import Awaitable, Callable

from .entity.observation import Observation
from .queue import InboundQueue


async def _no_processor(_observation: Observation) -> None:
    return None


class QueueWorker:
    def __init__(
        self, queue: InboundQueue,
        process: Callable[[Observation], Awaitable[None]] = _no_processor,
    ) -> None:
        self._queue = queue
        self._process = process
        self._task: asyncio.Task[None] | None = None

    @property
    def task(self) -> asyncio.Task[None]:
        if self._task is None:
            raise RuntimeError("Worker has not started")
        return self._task

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self) -> None:
        if self.is_running:
            raise RuntimeError("Worker is already running")
        self._task = asyncio.create_task(self._run(), name="argus:worker")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        await asyncio.gather(self._task, return_exceptions=True)
        self._task = None

    async def _run(self) -> None:
        while True:
            observation = await self._queue.get()
            try:
                await self._process(observation)
            finally:
                self._queue.task_done()
