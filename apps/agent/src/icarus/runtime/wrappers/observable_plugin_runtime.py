"""PluginRuntime 的低侵入观测实现。"""

from collections.abc import Awaitable, Callable
from typing import Any
from dataclasses import replace
from icarus.core.events import Event
from icarus.core.capabilities import CapabilityRequest
from icarus.core.hooks.points import preserve_event_control

from icarus.core.hooks.hook_dispatcher import HookDispatcher
from icarus.runtime.worker import (
    PluginRuntime,
)
from icarus.core.types import PublishedEvent


class ObservablePluginRuntime(PluginRuntime):
    def __init__(self, *args: Any, dispatcher: HookDispatcher, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._dispatcher = dispatcher
        dispatcher.declare_point("plugin.consume", "before", Event, validator=preserve_event_control)

    async def start(self) -> None:
        await self._lifecycle("start", super().start)

    async def stop(self, drain: bool = True) -> None:
        async def stop_runtime() -> None:
            await super(ObservablePluginRuntime, self).stop(drain=drain)

        await self._lifecycle("stop", stop_runtime)

    def start_background_work(
        self,
        name: str,
        operation: Callable[[], Awaitable[None]],
    ):
        data = {"plugin_id": self.plugin_id, "name": name}
        self._dispatcher.record("plugin.background_work", "before", data)

        async def observed() -> None:
            try:
                await operation()
            except Exception as error:
                await self._dispatcher.arecord(
                    "plugin.background_work",
                    "error",
                    {**data, **self._error_data(error)},
                )
                raise
            await self._dispatcher.arecord(
                "plugin.background_work", "after", data
            )

        return super().start_background_work(name, observed)

    async def _consume(self, published_event: PublishedEvent) -> None:
        if not isinstance(published_event.event, CapabilityRequest):
            event = await self._dispatcher.atrigger("plugin.consume", "before", published_event.event,
                data={"plugin_id": self.plugin_id, "event_type": type(published_event.event).__name__})
            published_event = replace(published_event, event=event)
        if not published_event.event.trace_event_flow:
            await super()._consume(published_event)
            return
        data = {
            "plugin_id": self.plugin_id,
            "published_event": published_event,
        }
        await self._dispatcher.arecord("plugin.consume", "before", data)
        try:
            await super()._consume(published_event)
        except Exception as error:
            await self._dispatcher.arecord(
                "plugin.consume",
                "error",
                {**data, **self._error_data(error)},
            )
            raise
        await self._dispatcher.arecord("plugin.consume", "after", data)

    async def _lifecycle(self, action: str, operation) -> None:
        data = {"plugin_id": self.plugin_id, "action": action}
        await self._dispatcher.arecord("plugin.lifecycle", "before", data)
        try:
            await operation()
        except Exception as error:
            await self._dispatcher.arecord(
                "plugin.lifecycle",
                "error",
                {**data, **self._error_data(error)},
            )
            raise
        await self._dispatcher.arecord("plugin.lifecycle", "after", data)

    @staticmethod
    def _error_data(error: Exception) -> dict[str, str]:
        return {
            "error_type": type(error).__name__,
            "error_message": str(error),
        }
