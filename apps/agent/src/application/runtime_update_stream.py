"""Bounded fan-out for device-level RuntimeUpdate subscribers."""

from __future__ import annotations

import asyncio
from uuid import uuid4

from apps.agent.src.runtime_update import RuntimeUpdate


class RuntimeUpdateOverflowError(RuntimeError):
    pass


class RuntimeUpdateSubscription:
    def __init__(
        self,
        stream: "RuntimeUpdateStream",
        subscription_id: str,
        capacity: int,
    ) -> None:
        self.subscription_id = subscription_id
        self._stream: RuntimeUpdateStream | None = stream
        self._queue: asyncio.Queue[RuntimeUpdate | BaseException | None] = (
            asyncio.Queue(maxsize=capacity)
        )
        self._read_lock = asyncio.Lock()
        self._closed = False

    @property
    def closed(self) -> bool:
        return self._closed

    async def next_update(self) -> RuntimeUpdate:
        async with self._read_lock:
            if self._closed and self._queue.empty():
                raise RuntimeError("Runtime update subscription is closed")
            item = await self._queue.get()
            if item is None:
                raise RuntimeError("Runtime update subscription is closed")
            if isinstance(item, BaseException):
                raise item
            return item

    def close(self) -> None:
        stream = self._stream
        if stream is not None:
            stream._close_subscription(self)

    def _publish(self, update: RuntimeUpdate) -> bool:
        if self._closed:
            return False
        try:
            self._queue.put_nowait(update)
            return True
        except asyncio.QueueFull:
            self._close(RuntimeUpdateOverflowError("Runtime update subscription overflow"))
            return False

    def _close(self, error: BaseException | None = None) -> None:
        if self._closed:
            return
        self._closed = True
        self._stream = None
        while True:
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        self._queue.put_nowait(error)


class RuntimeUpdateStream:
    def __init__(self, capacity: int = 4096) -> None:
        if capacity < 1:
            raise ValueError("Runtime update queue capacity must be positive")
        self.capacity = capacity
        self._subscriptions: dict[str, RuntimeUpdateSubscription] = {}
        self._closed = False

    def subscribe(self) -> RuntimeUpdateSubscription:
        if self._closed:
            raise RuntimeError("Runtime update stream is closed")
        subscription_id = uuid4().hex
        subscription = RuntimeUpdateSubscription(
            self, subscription_id, self.capacity
        )
        self._subscriptions[subscription_id] = subscription
        return subscription

    async def publish(self, update: RuntimeUpdate) -> None:
        if self._closed:
            return
        for subscription in tuple(self._subscriptions.values()):
            if not subscription._publish(update):
                self._subscriptions.pop(subscription.subscription_id, None)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        subscriptions = tuple(self._subscriptions.values())
        self._subscriptions.clear()
        for subscription in subscriptions:
            subscription._close()

    def _close_subscription(
        self, subscription: RuntimeUpdateSubscription
    ) -> None:
        if self._subscriptions.pop(subscription.subscription_id, None) is not None:
            subscription._close()
