"""Bounded local delivery with explicit closure and slow-consumer isolation."""

import asyncio
from typing import Generic, TypeVar

T = TypeVar("T")
DEFAULT_QUEUE_CAPACITY = 4096
_CLOSED = object()


class MailboxOverflowError(RuntimeError):
    pass


class BoundedMailbox(Generic[T]):
    def __init__(self, capacity: int = DEFAULT_QUEUE_CAPACITY) -> None:
        if isinstance(capacity, bool) or capacity < 1:
            raise ValueError("Mailbox capacity must be positive")
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=capacity)
        self._read_lock = asyncio.Lock()
        self.closed = False

    def offer(self, item: T) -> bool:
        if self.closed:
            return False
        try:
            self._queue.put_nowait(item)
            return True
        except asyncio.QueueFull:
            self.close(MailboxOverflowError("Runtime update subscription overflow"))
            return False

    async def next(self) -> T:
        async with self._read_lock:
            if self.closed and self._queue.empty():
                raise RuntimeError("Mailbox is closed")
            item = await self._queue.get()
            if item is _CLOSED:
                raise RuntimeError("Mailbox is closed")
            if isinstance(item, BaseException):
                raise item
            return item

    def close(self, error: BaseException | None = None) -> None:
        if self.closed:
            return
        self.closed = True
        while not self._queue.empty():
            self._queue.get_nowait()
        self._queue.put_nowait(error if error is not None else _CLOSED)


class BoundedFanout(Generic[T]):
    def __init__(self, capacity: int = DEFAULT_QUEUE_CAPACITY) -> None:
        if capacity < 1:
            raise ValueError("Fanout capacity must be positive")
        self.capacity = capacity
        self._subscriptions: set[BoundedMailbox[T]] = set()
        self.closed = False

    def subscribe(self) -> BoundedMailbox[T]:
        if self.closed:
            raise RuntimeError("Fanout is closed")
        mailbox: BoundedMailbox[T] = BoundedMailbox(self.capacity)
        self._subscriptions.add(mailbox)
        return mailbox

    def publish(self, item: T) -> None:
        if self.closed:
            return
        for mailbox in tuple(self._subscriptions):
            if not mailbox.offer(item):
                self._subscriptions.discard(mailbox)

    def unsubscribe(self, mailbox: BoundedMailbox[T]) -> None:
        self._subscriptions.discard(mailbox)
        mailbox.close()

    def close(self) -> None:
        self.closed = True
        for mailbox in self._subscriptions:
            mailbox.close()
        self._subscriptions.clear()
