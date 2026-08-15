"""Plugin 抽象。"""

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable

from apps.agent.src.agent_orchestration.events import Event
from apps.agent.src.agent_orchestration.plugin_runtime.types import PluginId


EventPublisher = Callable[[Event], Awaitable[None]]


class BasePlugin(ABC):
    """所有编排插件共享的最小异步接口。"""

    def __init__(self, plugin_id: PluginId) -> None:
        if not plugin_id.strip():
            raise ValueError("plugin_id cannot be empty")
        self._plugin_id = plugin_id
        self._publisher: EventPublisher | None = None

    @property
    def plugin_id(self) -> PluginId:
        return self._plugin_id

    async def start(self) -> None:
        pass

    @abstractmethod
    async def consume(
        self,
        source_plugin_id: PluginId,
        event: Event,
    ) -> None:
        ...

    async def stop(self) -> None:
        pass

    async def drain(self) -> None:
        pass

    async def publish(self, event: Event) -> None:
        if self._publisher is None:
            raise RuntimeError(f"Plugin is not bound to EventBus: {self.plugin_id}")
        await self._publisher(event)

    def bind_publisher(self, publisher: EventPublisher) -> None:
        self._publisher = publisher

    def unbind_publisher(self) -> None:
        self._publisher = None
