"""Plugin Runtime 的统一组装与生命周期入口。"""

import asyncio

from apps.agent.src.agent_orchestration.hooks.hook_dispatcher import HookDispatcher
from apps.agent.src.agent_orchestration.plugin_runtime.base_plugin import BasePlugin
from apps.agent.src.agent_orchestration.plugin_runtime.event_bus import EventBus
from apps.agent.src.agent_orchestration.plugin_runtime.plugin_registry import (
    PluginRegistry,
)
from apps.agent.src.agent_orchestration.plugin_runtime.plugin_runtime import (
    PluginRuntime,
)
from apps.agent.src.agent_orchestration.plugin_runtime.types import (
    PluginRuntimeSnapshot,
    Subscription,
)
from apps.agent.src.agent_orchestration.plugin_runtime.wrappers.observable_event_bus import (
    ObservableEventBus,
)
from apps.agent.src.agent_orchestration.plugin_runtime.wrappers.observable_plugin_runtime import (
    ObservablePluginRuntime,
)


class PluginManager:
    """注册、订阅、启动和停止完整 Plugin Runtime。"""

    def __init__(
        self,
        registry: PluginRegistry | None = None,
        inbox_maxsize: int = 0,
        ingress_maxsize: int = 0,
        hook_dispatcher: HookDispatcher | None = None,
    ) -> None:
        self.registry = registry or PluginRegistry()
        self.inbox_maxsize = inbox_maxsize
        self.hook_dispatcher = hook_dispatcher
        self._runtimes: dict[str, PluginRuntime] = {}
        if hook_dispatcher is None:
            self.event_bus = EventBus(
                self.registry,
                self._runtimes,
                ingress_maxsize=ingress_maxsize,
            )
        else:
            self.event_bus = ObservableEventBus(
                self.registry,
                self._runtimes,
                ingress_maxsize=ingress_maxsize,
                dispatcher=hook_dispatcher,
            )
        self._started = False

    @property
    def is_running(self) -> bool:
        return self._started

    def register(self, plugin: BasePlugin) -> PluginRuntime:
        if self._started:
            raise RuntimeError("Plugins must be registered before manager start")
        self.registry.register(plugin)
        if self.hook_dispatcher is None:
            runtime = PluginRuntime(
                plugin,
                self.registry,
                inbox_maxsize=self.inbox_maxsize,
            )
        else:
            runtime = ObservablePluginRuntime(
                plugin,
                self.registry,
                inbox_maxsize=self.inbox_maxsize,
                dispatcher=self.hook_dispatcher,
            )
        self._runtimes[plugin.plugin_id] = runtime

        async def publish(event) -> None:
            await self.event_bus.publish(plugin.plugin_id, event)

        plugin.bind_publisher(publish)
        return runtime

    def unregister(self, plugin_id: str) -> BasePlugin:
        runtime = self._runtimes.get(plugin_id)
        if runtime is None:
            raise KeyError(f"Plugin runtime is not registered: {plugin_id}")
        plugin = self.registry.unregister(plugin_id)
        plugin.unbind_publisher()
        del self._runtimes[plugin_id]
        return plugin

    def subscribe(
        self,
        subscriber_plugin_id: str,
        source_plugin_id: str,
    ) -> Subscription:
        return self.registry.subscribe(
            subscriber_plugin_id=subscriber_plugin_id,
            source_plugin_id=source_plugin_id,
        )

    def unsubscribe(self, subscription_id: str) -> Subscription:
        return self.registry.unsubscribe(subscription_id)

    def get_runtime(self, plugin_id: str) -> PluginRuntime:
        try:
            return self._runtimes[plugin_id]
        except KeyError as error:
            raise KeyError(f"Plugin runtime is not registered: {plugin_id}") from error

    def get_runtime_snapshot(self, plugin_id: str) -> PluginRuntimeSnapshot:
        return self.get_runtime(plugin_id).snapshot()

    async def start(self) -> None:
        if self._started:
            return
        started_runtimes: list[PluginRuntime] = []
        try:
            for runtime in self._runtimes.values():
                await runtime.start()
                started_runtimes.append(runtime)
            await self.event_bus.start()
        except Exception:
            await self.event_bus.stop(drain=False)
            await asyncio.gather(
                *(
                    runtime.stop(drain=False)
                    for runtime in reversed(started_runtimes)
                ),
                return_exceptions=True,
            )
            raise
        else:
            self._started = True

    async def stop(self, timeout: float | None = None) -> None:
        if not self._started:
            return

        async def drain_and_stop() -> None:
            await self._drain_until_idle()
            await self.event_bus.stop(drain=False)
            for runtime in self._runtimes.values():
                await runtime.stop(drain=False)

        try:
            if timeout is None:
                await drain_and_stop()
            else:
                await asyncio.wait_for(drain_and_stop(), timeout=timeout)
        except TimeoutError:
            await self.event_bus.stop(drain=False)
            for runtime in self._runtimes.values():
                await runtime.stop(drain=False)
            raise
        finally:
            self._started = False

    async def _drain_until_idle(self) -> None:
        while True:
            await self.event_bus.drain()
            await asyncio.gather(
                *(runtime.drain() for runtime in self._runtimes.values())
            )
            if self.event_bus.pending_count == 0 and all(
                runtime.pending_count == 0
                for runtime in self._runtimes.values()
            ):
                return

