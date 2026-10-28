"""EventBus 的低侵入观测实现。"""

from typing import Any

from icarus.core.events import Event
from icarus.core.hooks.hook_context import hook_context
from icarus.core.hooks.hook_dispatcher import HookDispatcher
from icarus.runtime.event_bus import EventBus
from icarus.core.types import PublishedEvent


class ObservableEventBus(EventBus):
    def __init__(self, *args: Any, dispatcher: HookDispatcher, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._dispatcher = dispatcher

    async def publish(self, source_plugin_id: str, event: Event) -> None:
        if not event.trace_event_flow:
            await super().publish(source_plugin_id, event)
            return
        data = {"source_plugin_id": source_plugin_id, "event": event}
        await self._dispatcher.arecord("event.publish", "before", data)
        try:
            await super().publish(source_plugin_id, event)
        except Exception as error:
            await self._dispatcher.arecord(
                "event.publish",
                "error",
                {**data, **self._error_data(error)},
            )
            raise
        await self._dispatcher.arecord("event.publish", "after", data)

    async def _route(self, published_event: PublishedEvent) -> None:
        with hook_context(
            published_event.hook_context,
            run_id=published_event.hook_run_id,
        ):
            if not published_event.event.trace_event_flow:
                await super()._route(published_event)
                return
            data = {"published_event": published_event}
            await self._dispatcher.arecord("event.route", "before", data)
            try:
                await super()._route(published_event)
            except Exception as error:
                await self._dispatcher.arecord(
                    "event.route",
                    "error",
                    {**data, **self._error_data(error)},
                )
                raise
            await self._dispatcher.arecord("event.route", "after", data)

    @staticmethod
    def _error_data(error: Exception) -> dict[str, str]:
        return {
            "error_type": type(error).__name__,
            "error_message": str(error),
        }
