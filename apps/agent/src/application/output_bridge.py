"""AgentRuntimeService 的输出事件桥接。"""

import asyncio
from uuid import uuid4

from apps.agent.src.agent_orchestration.events import Event
from apps.agent.src.agent_orchestration.plugin_runtime import BasePlugin


OutputEvent = tuple[str, Event]


class OutputEventSubscription:
    """一个实时输出订阅及其独立缓冲队列。"""

    def __init__(
        self,
        bridge: "OutputBridgePlugin",
        subscription_id: str,
    ) -> None:
        self.subscription_id = subscription_id
        self._bridge: OutputBridgePlugin | None = bridge
        self._events: asyncio.Queue[OutputEvent | None] = asyncio.Queue()
        self._read_lock = asyncio.Lock()
        self._closed = False

    @property
    def closed(self) -> bool:
        return self._closed

    async def next_event(self) -> OutputEvent:
        async with self._read_lock:
            if self._closed:
                raise RuntimeError("Output event subscription is closed")
            item = await self._events.get()
            if item is None:
                raise RuntimeError("Output event subscription is closed")
            return item

    def close(self) -> None:
        bridge = self._bridge
        if bridge is not None:
            bridge._close_subscription(self)

    def _publish(self, item: OutputEvent) -> None:
        if not self._closed:
            self._events.put_nowait(item)

    def _close(self) -> int:
        if self._closed:
            return 0
        self._closed = True
        self._bridge = None
        discarded = 0
        while True:
            try:
                self._events.get_nowait()
            except asyncio.QueueEmpty:
                break
            else:
                discarded += 1
        self._events.put_nowait(None)
        return discarded


class OutputBridgePlugin(BasePlugin):
    """将内部 Plugin Event 广播到应用层实时订阅。"""

    def __init__(self, plugin_id: str = "output-bridge") -> None:
        super().__init__(plugin_id)
        self._subscriptions: dict[str, OutputEventSubscription] = {}

    def subscribe(self) -> OutputEventSubscription:
        subscription = OutputEventSubscription(self, uuid4().hex)
        self._subscriptions[subscription.subscription_id] = subscription
        return subscription

    async def consume(
        self,
        source_plugin_id: str,
        event: Event,
    ) -> None:
        item = (source_plugin_id, event)
        for subscription in tuple(self._subscriptions.values()):
            subscription._publish(item)

    async def stop(self) -> None:
        self.close_subscriptions()

    def close_subscriptions(self) -> int:
        subscriptions = tuple(self._subscriptions.values())
        self._subscriptions.clear()
        return sum(subscription._close() for subscription in subscriptions)

    def _close_subscription(
        self,
        subscription: OutputEventSubscription,
    ) -> None:
        registered = self._subscriptions.get(subscription.subscription_id)
        if registered is not subscription:
            return
        del self._subscriptions[subscription.subscription_id]
        subscription._close()
