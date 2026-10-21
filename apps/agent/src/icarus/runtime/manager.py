"""Plugin Runtime 的统一组装与生命周期入口。"""

import asyncio
from collections.abc import Iterable

from icarus.core.hooks.hook_dispatcher import HookDispatcher
from icarus.core.plugin import BasePlugin
from icarus.core.capabilities import CapabilityRequest, CapabilityResponse, CapabilityRequester
from icarus.runtime.loop_runner import RuntimeLoopRunner
from icarus.runtime.event_bus import EventBus
from icarus.runtime.registry import (
    PluginRegistry,
)
from icarus.runtime.worker import (
    PluginRuntime,
)
from icarus.core.types import (
    PluginRuntimeSnapshot,
    Subscription,
)
from icarus.runtime.wrappers.observable_event_bus import (
    ObservableEventBus,
)
from icarus.runtime.wrappers.observable_plugin_runtime import (
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
        self._capability_providers: dict[str, str] = {}
        self._capability_consumers: dict[str, set[str]] = {}
        self.loop_runner = RuntimeLoopRunner()

    def bind_capability(self, capability_id: str, provider_plugin_id: str, *, required_by: set[str]) -> None:
        if self.inbox_maxsize > 0:
            raise ValueError("Directed capabilities require an unbounded inbox to avoid router head-of-line deadlock")
        if capability_id in self._capability_providers:
            raise ValueError(f"Duplicate capability provider: {capability_id}")
        self._capability_providers[capability_id] = provider_plugin_id
        self._capability_consumers[capability_id] = set(required_by)

    @property
    def is_running(self) -> bool:
        return self._started

    def register(
        self,
        plugin: BasePlugin,
        *,
        published_event_types: Iterable[type] | None = None,
        consumed_event_types: Iterable[type] | None = None,
    ) -> PluginRuntime:
        if self._started:
            raise RuntimeError("Plugins must be registered before manager start")
        self.registry.register(plugin)
        if self.hook_dispatcher is None:
            runtime = PluginRuntime(
                plugin,
                self.registry,
                inbox_maxsize=self.inbox_maxsize,
                consumed_event_types=consumed_event_types,
            )
        else:
            runtime = ObservablePluginRuntime(
                plugin,
                self.registry,
                inbox_maxsize=self.inbox_maxsize,
                dispatcher=self.hook_dispatcher,
                consumed_event_types=consumed_event_types,
            )
        self._runtimes[plugin.plugin_id] = runtime

        allowed = (
            None
            if published_event_types is None
            else frozenset(published_event_types)
        )

        async def publish(event) -> None:
            if isinstance(event, (CapabilityRequest, CapabilityResponse)):
                raise RuntimeError("Capability traffic must use the directed capability path")
            if allowed is not None and type(event) not in allowed:
                raise RuntimeError(
                    "Plugin published an undeclared Event: "
                    f"plugin_id={plugin.plugin_id} "
                    f"event={type(event).__module__}.{type(event).__name__}"
                )
            await self.event_bus.publish(plugin.plugin_id, event)

        plugin.bind_publisher(publish)
        plugin._loop_runner = self.loop_runner
        plugin.bind_background_work_starter(runtime.start_background_work)

        def resolve(request: CapabilityRequest) -> str:
            if not self._started:
                raise RuntimeError("Runtime is not ready for capability calls")
            if plugin.plugin_id not in self._capability_consumers.get(request.capability_id, set()):
                raise RuntimeError("Plugin did not require this capability")
            return self._capability_providers[request.capability_id]

        async def send(destination: str, event) -> None:
            await self.event_bus.publish_to(plugin.plugin_id, destination, event)

        plugin.bind_requester(CapabilityRequester(
            source_plugin_id=plugin.plugin_id, resolve=resolve, send=send,
        ), send)
        return runtime

    def unregister(self, plugin_id: str) -> BasePlugin:
        runtime = self._runtimes.get(plugin_id)
        if runtime is None:
            raise KeyError(f"Plugin runtime is not registered: {plugin_id}")
        plugin = self.registry.unregister(plugin_id)
        if plugin._requester is not None:
            plugin._requester.close()
        for capability_id, provider in tuple(self._capability_providers.items()):
            if provider == plugin_id:
                self._capability_providers.pop(capability_id, None)
                self._capability_consumers.pop(capability_id, None)
            else:
                self._capability_consumers.get(capability_id, set()).discard(plugin_id)
        plugin.unbind_publisher()
        plugin.unbind_background_work_starter()
        del self._runtimes[plugin_id]
        return plugin

    def subscribe(
        self,
        subscriber_plugin_id: str,
        source_plugin_id: str,
    ) -> Subscription:
        if self._started:
            raise RuntimeError(
                "Subscriptions must be registered before manager start"
            )
        return self.registry.subscribe(
            subscriber_plugin_id=subscriber_plugin_id,
            source_plugin_id=source_plugin_id,
        )

    def unsubscribe(self, subscription_id: str) -> Subscription:
        if self._started:
            raise RuntimeError(
                "Subscriptions cannot be removed while manager is running"
            )
        return self.registry.unsubscribe(subscription_id)

    def get_runtime(self, plugin_id: str) -> PluginRuntime:
        try:
            return self._runtimes[plugin_id]
        except KeyError as error:
            raise KeyError(f"Plugin runtime is not registered: {plugin_id}") from error

    def get_runtime_snapshot(self, plugin_id: str) -> PluginRuntimeSnapshot:
        return self.get_runtime(plugin_id).snapshot()

    def snapshots(self) -> tuple[PluginRuntimeSnapshot, ...]:
        return tuple(runtime.snapshot() for runtime in self._runtimes.values())

    async def start(self) -> None:
        if self._started:
            return
        self.loop_runner.bind()
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

    async def start_plugin(self, plugin_id: str) -> None:
        if self._started:
            raise RuntimeError("Plugins must start before manager activation")
        await self.get_runtime(plugin_id).start()

    async def quiesce(self) -> None:
        if not self._started:
            return
        errors: list[BaseException] = []
        for runtime in reversed(tuple(self._runtimes.values())):
            if runtime.plugin._requester is not None:
                runtime.plugin._requester.close()
            try:
                await runtime.quiesce()
            except BaseException as error:
                errors.append(error)
        if errors:
            raise RuntimeError(
                "Plugin Runtime quiesce failed: "
                + "; ".join(str(error) for error in errors)
            )

    async def drain(self) -> None:
        if not self._started:
            return
        await self._drain_until_idle()

    async def drain_plugin(self, plugin_id: str) -> None:
        if not self._started:
            return
        await self.get_runtime(plugin_id).drain()

    async def stop(
        self,
        timeout: float | None = None,
        *,
        drain: bool = True,
    ) -> None:
        if not self._started:
            return

        async def drain_and_stop() -> None:
            for runtime in self._runtimes.values():
                if runtime.plugin._requester is not None:
                    runtime.plugin._requester.close()
            if drain:
                await self._drain_until_idle()
            await self.event_bus.stop(drain=False)
            errors: list[BaseException] = []
            for runtime in reversed(tuple(self._runtimes.values())):
                try:
                    await runtime.stop(drain=False)
                except BaseException as error:
                    errors.append(error)
            if errors:
                raise RuntimeError(
                    "Plugin Runtime cleanup failed: "
                    + "; ".join(str(error) for error in errors)
                )

        try:
            if timeout is None:
                await drain_and_stop()
            else:
                await asyncio.wait_for(drain_and_stop(), timeout=timeout)
        except TimeoutError:
            await self.event_bus.stop(drain=False)
            errors: list[BaseException] = []
            for runtime in reversed(tuple(self._runtimes.values())):
                try:
                    await runtime.stop(drain=False)
                except BaseException as error:
                    errors.append(error)
            if errors:
                raise RuntimeError(
                    "Plugin Runtime timeout cleanup failed: "
                    + "; ".join(str(error) for error in errors)
                )
            raise
        finally:
            self._started = False
            self.loop_runner.close()

    async def _drain_until_idle(self) -> None:
        while True:
            await self.event_bus.drain()
            results = await asyncio.gather(
                *(runtime.drain() for runtime in self._runtimes.values()),
                return_exceptions=True,
            )
            errors = [
                result
                for result in results
                if isinstance(result, BaseException)
            ]
            if errors:
                raise RuntimeError(
                    "Plugin Runtime drain failed: "
                    + "; ".join(str(error) for error in errors)
                )
            if self.event_bus.pending_count == 0 and all(
                runtime.pending_count == 0
                for runtime in self._runtimes.values()
            ):
                return

