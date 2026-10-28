"""Typed application facade over framework-owned bounded delivery."""

from uuid import uuid4

from agent_app.contracts.prompt import SESSION_INSTRUCTIONS_TYPE
from icarus.core.runtime_update import RuntimeUpdate
from icarus.runtime.fanout import BoundedFanout, BoundedMailbox, DEFAULT_QUEUE_CAPACITY, MailboxOverflowError

RuntimeUpdateOverflowError = MailboxOverflowError


class RuntimeUpdateSubscription:
    def __init__(self, stream: "RuntimeUpdateStream", mailbox: BoundedMailbox[RuntimeUpdate]) -> None:
        self.subscription_id = uuid4().hex
        self._stream = stream
        self._mailbox = mailbox

    @property
    def closed(self) -> bool:
        return self._mailbox.closed

    async def next_update(self) -> RuntimeUpdate:
        return await self._mailbox.next()

    def close(self) -> None:
        self._stream._fanout.unsubscribe(self._mailbox)


class RuntimeUpdateStream:
    def __init__(self, capacity: int = DEFAULT_QUEUE_CAPACITY) -> None:
        self.capacity = capacity
        self._fanout: BoundedFanout[RuntimeUpdate] = BoundedFanout(capacity)

    def configure_capacity(self, capacity: int) -> None:
        if capacity == self.capacity:
            return
        if self._fanout._subscriptions:
            for mailbox in self._fanout._subscriptions:
                if not mailbox._queue.empty():
                    raise RuntimeError("Cannot resize an active subscription")
                mailbox._queue = type(mailbox._queue)(maxsize=capacity)
            self._fanout.capacity = capacity
            self.capacity = capacity
            return
        self._fanout = BoundedFanout(capacity)
        self.capacity = capacity

    def subscribe(self) -> RuntimeUpdateSubscription:
        return RuntimeUpdateSubscription(self, self._fanout.subscribe())

    async def publish(self, update: RuntimeUpdate) -> None:
        if update.type == SESSION_INSTRUCTIONS_TYPE:
            raise ValueError("Session instructions are private")
        self._fanout.publish(update)

    def close(self) -> None:
        self._fanout.close()
