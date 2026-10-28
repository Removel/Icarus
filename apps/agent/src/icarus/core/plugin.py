"""Plugin 抽象。"""

import asyncio
from abc import ABC
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, ClassVar
from pydantic import BaseModel
from icarus.core.capabilities.catalog import CapabilityContract

from icarus.core.tools.base_tool import BaseTool

from icarus.core.events import Event
from icarus.core.capabilities import CapabilityRequest, CapabilityResponse, CapabilityRequester
from icarus.core.types import PluginId


EventPublisher = Callable[[Event], Awaitable[None]]
BackgroundOperation = Callable[[], Awaitable[None]]
BackgroundWorkStarter = Callable[
    [str, BackgroundOperation], asyncio.Task[None]
]


class PluginContractError(ValueError):
    pass


def handles(*event_types: type[Event], optional: bool = False):
    if not event_types or any(not issubclass(t, Event) for t in event_types):
        raise PluginContractError("handlers must declare Event types")

    def decorate(method):
        method.__handled_events__ = tuple(event_types)
        method.__optional_handler__ = optional
        return method
    return decorate


class BasePlugin(ABC):
    """所有编排插件共享的最小异步接口。"""

    def __init__(self, plugin_id: PluginId) -> None:
        if not plugin_id.strip():
            raise ValueError("plugin_id cannot be empty")
        self._plugin_id = plugin_id
        self._publisher: EventPublisher | None = None
        self._background_work_starter: BackgroundWorkStarter | None = None
        self._requester: CapabilityRequester | None = None
        self._response_publisher = None
        self._tools: tuple[BaseTool, ...] = ()
        self._loop_runner: Any = None

    @property
    def plugin_id(self) -> PluginId:
        return self._plugin_id

    async def start(self) -> None:
        pass

    provided_capabilities: ClassVar[tuple[type[CapabilityContract], ...]] = ()
    config_schema: ClassVar[type[BaseModel] | None] = None

    @classmethod
    def handler_bindings(cls) -> dict[type[Event], str]:
        bindings: dict[type[Event], str] = {}
        for parent in reversed(cls.__mro__):
            for name, method in vars(parent).items():
                for event_type in getattr(method, "__handled_events__", ()):
                    previous = bindings.get(event_type)
                    if previous is not None and previous != name:
                        raise PluginContractError(f"Duplicate handler for {event_type.__name__}")
                    bindings[event_type] = name
        return bindings

    def validate_manifest(self, manifest, consumed_types, capability_catalog=None) -> None:
        if type(self).consume is not BasePlugin.consume:
            raise PluginContractError("Manifest plugins must declare handlers instead of overriding consume")
        bindings = self.handler_bindings()
        ordinary = {kind for kind in bindings if not issubclass(kind, (CapabilityRequest, CapabilityResponse))}
        optional = {kind for kind, name in bindings.items()
                    if getattr(getattr(type(self), name), "__optional_handler__", False)}
        expected = set(consumed_types)
        if ordinary - optional != expected - optional or not expected <= ordinary:
            raise PluginContractError("Plugin handler declarations do not match manifest consumed events")
        if any(issubclass(kind, CapabilityResponse) for kind in bindings):
            raise PluginContractError("Capability responses cannot have handlers")
        if capability_catalog is not None:
            provided = {c.capability_id: c for c in self.provided_capabilities}
            declared = {c.capability_id: c.version for c in manifest.provided_capabilities}
            if set(provided) != set(declared):
                raise PluginContractError("Plugin capability declarations do not match manifest")
            expected_requests = set()
            for key, contract in provided.items():
                if capability_catalog.resolve(key) is not contract:
                    raise PluginContractError("Plugin capability class must match the authoritative catalog")
                if contract.version != declared[key]:
                    raise PluginContractError("Plugin capability version does not match manifest")
                if contract.access_mode == "event":
                    expected_requests.add(contract.request_type)
            actual_requests = {kind for kind in bindings if issubclass(kind, CapabilityRequest)}
            if actual_requests != expected_requests:
                raise PluginContractError("Capability request handlers do not match provided contracts")

    def build_tools(self) -> tuple[BaseTool, ...]:
        return ()

    async def on_session_start(self) -> None:
        pass

    async def dispatch(self, source_plugin_id: PluginId, event: Event) -> None:
        name = self.handler_bindings().get(type(event))
        if name is None:
            raise PluginContractError(f"No handler for {type(event).__name__}")
        output = await getattr(self, name)(source_plugin_id, event)
        if output is None:
            if isinstance(event, CapabilityRequest):
                raise PluginContractError("Capability handler must return exactly one response")
            return
        if isinstance(event, CapabilityRequest) and len(output) != 1:
            raise PluginContractError("Capability handler must return exactly one response")
        if not isinstance(output, Sequence) or any(not isinstance(item, Event) for item in output):
            raise PluginContractError("Handler must return a sequence of Events or None")
        for item in output:
            if isinstance(event, CapabilityRequest):
                if type(item) is not event.response_type or item.correlation_id != event.correlation_id or item.task_id != event.task_id:
                    raise PluginContractError("Capability handler returned a mismatched response")
                if self._response_publisher is None:
                    raise PluginContractError("Capability response transport is not bound")
                await self._response_publisher(source_plugin_id, item)
            else:
                await self.publish(item)

    async def request(self, event: CapabilityRequest) -> CapabilityResponse:
        if self._requester is None:
            raise RuntimeError("Plugin capability requester is not bound")
        return await self._requester.request(event)

    def bind_requester(self, requester: CapabilityRequester, response_publisher) -> None:
        self._requester = requester
        self._response_publisher = response_publisher

    async def consume(self, source_plugin_id: PluginId, event: Event) -> None:
        await self.dispatch(source_plugin_id, event)

    async def stop(self) -> None:
        pass

    async def drain(self) -> None:
        pass

    async def quiesce(self) -> None:
        """Stop accepting new domain work while allowing cleanup events."""

        pass

    def accepts_event(
        self,
        source_plugin_id: PluginId,
        event: Event,
    ) -> bool:
        """Return whether a source-routed Event should enter this Plugin inbox."""

        return True

    async def publish(self, event: Event) -> None:
        if self._publisher is None:
            raise RuntimeError(f"Plugin is not bound to EventBus: {self.plugin_id}")
        await self._publisher(event)

    def bind_publisher(self, publisher: EventPublisher) -> None:
        self._publisher = publisher

    def unbind_publisher(self) -> None:
        self._publisher = None

    def start_background_work(
        self,
        operation: BackgroundOperation,
        *,
        name: str,
    ) -> asyncio.Task[None]:
        starter = self._background_work_starter
        if starter is None:
            raise RuntimeError(
                f"Plugin is not bound to a Runtime: {self.plugin_id}"
            )
        return starter(name, operation)

    def bind_background_work_starter(
        self,
        starter: BackgroundWorkStarter,
    ) -> None:
        self._background_work_starter = starter

    def unbind_background_work_starter(self) -> None:
        self._background_work_starter = None
