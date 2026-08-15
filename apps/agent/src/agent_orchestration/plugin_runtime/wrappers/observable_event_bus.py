"""EventBus 的低侵入观测实现。"""

from typing import Any

from apps.agent.src.agent_orchestration.events import Event
from apps.agent.src.agent_orchestration.hooks.hook_dispatcher import HookDispatcher
from apps.agent.src.agent_orchestration.plugin_runtime.event_bus import EventBus
from apps.agent.src.agent_orchestration.plugin_runtime.types import PublishedEvent


class ObservableEventBus(EventBus):
    def __init__(self, *args: Any, dispatcher: HookDispatcher, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._dispatcher = dispatcher

    async def publish(self, source_plugin_id: str, event: Event) -> None:
        data = {"source_plugin_id": source_plugin_id, "event": event}
        await self._dispatcher.atrigger("event.publish", "before", data)
        try:
            await super().publish(source_plugin_id, event)
        except Exception as error:
            await self._dispatcher.atrigger(
                "event.publish",
                "error",
                {**data, **self._error_data(error)},
            )
            raise
        await self._dispatcher.atrigger("event.publish", "after", data)

    async def _route(self, published_event: PublishedEvent) -> None:
        data = {"published_event": published_event}
        await self._dispatcher.atrigger("event.route", "before", data)
        try:
            await super()._route(published_event)
        except Exception as error:
            await self._dispatcher.atrigger(
                "event.route",
                "error",
                {**data, **self._error_data(error)},
            )
            raise
        await self._dispatcher.atrigger("event.route", "after", data)

    @staticmethod
    def _error_data(error: Exception) -> dict[str, str]:
        return {
            "error_type": type(error).__name__,
            "error_message": str(error),
        }
