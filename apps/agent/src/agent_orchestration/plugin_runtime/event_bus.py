"""Plugin 之间的异步事件通道。"""

import asyncio
import logging

from apps.agent.src.agent_orchestration.events import Event
from apps.agent.src.agent_orchestration.plugin_runtime.plugin_registry import (
    PluginRegistry,
)
from apps.agent.src.agent_orchestration.plugin_runtime.plugin_runtime import (
    PluginRuntime,
)
from apps.agent.src.agent_orchestration.plugin_runtime.types import (
    PluginStatus,
    PublishedEvent,
)


logger = logging.getLogger(__name__)


class EventBus:
    """只按来源 Plugin 将 Event 路由到目标 Runtime。"""

    def __init__(
        self,
        registry: PluginRegistry,
        runtimes: dict[str, PluginRuntime],
        ingress_maxsize: int = 0,
    ) -> None:
        self.registry = registry
        self.runtimes = runtimes
        self._ingress: asyncio.Queue[PublishedEvent] = asyncio.Queue(
            maxsize=ingress_maxsize
        )
        self._router: asyncio.Task[None] | None = None
        self._accepting = False
        self._accepted_count = 0
        self._routed_count = 0

    @property
    def is_running(self) -> bool:
        return self._router is not None and not self._router.done()

    @property
    def pending_count(self) -> int:
        return self._accepted_count - self._routed_count

    async def start(self) -> None:
        if self.is_running:
            return
        self._accepting = True
        self._router = asyncio.create_task(
            self._route_loop(),
            name="plugin-event-bus",
        )

    async def publish(self, source_plugin_id: str, event: Event) -> None:
        if not self._accepting:
            raise RuntimeError("EventBus is not accepting events")
        if not self.registry.contains(source_plugin_id):
            raise KeyError(f"Source plugin is not registered: {source_plugin_id}")
        if self.registry.get_status(source_plugin_id) != PluginStatus.RUNNING:
            raise RuntimeError(f"Source plugin is not running: {source_plugin_id}")
        await self._ingress.put(PublishedEvent(source_plugin_id, event))
        self._accepted_count += 1

    async def drain(self) -> None:
        await self._ingress.join()

    async def stop(self, drain: bool = True) -> None:
        if self._router is None:
            self._accepting = False
            return
        self._accepting = False
        if drain:
            await self.drain()
        self._router.cancel()
        await asyncio.gather(self._router, return_exceptions=True)
        self._router = None

    async def _route_loop(self) -> None:
        while True:
            published_event = await self._ingress.get()
            try:
                await self._route(published_event)
            finally:
                self._routed_count += 1
                self._ingress.task_done()

    async def _route(self, published_event: PublishedEvent) -> None:
        for subscriber_id in self.registry.get_subscriber_ids(
            published_event.source_plugin_id
        ):
            runtime = self.runtimes.get(subscriber_id)
            if runtime is None:
                logger.error(
                    "Subscriber runtime is missing: plugin_id=%s",
                    subscriber_id,
                )
                continue
            try:
                await runtime.enqueue(published_event)
            except Exception:
                logger.exception(
                    "Failed to route event: source=%s subscriber=%s event_id=%s",
                    published_event.source_plugin_id,
                    subscriber_id,
                    published_event.event.event_id,
                )
