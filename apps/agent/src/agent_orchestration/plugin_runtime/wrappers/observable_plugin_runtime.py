"""PluginRuntime 的低侵入观测实现。"""

from typing import Any

from apps.agent.src.agent_orchestration.hooks.hook_dispatcher import HookDispatcher
from apps.agent.src.agent_orchestration.plugin_runtime.plugin_runtime import (
    PluginRuntime,
)
from apps.agent.src.agent_orchestration.plugin_runtime.types import PublishedEvent


class ObservablePluginRuntime(PluginRuntime):
    def __init__(self, *args: Any, dispatcher: HookDispatcher, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._dispatcher = dispatcher

    async def start(self) -> None:
        await self._lifecycle("start", super().start)

    async def stop(self, drain: bool = True) -> None:
        async def stop_runtime() -> None:
            await super(ObservablePluginRuntime, self).stop(drain=drain)

        await self._lifecycle("stop", stop_runtime)

    async def _consume(self, published_event: PublishedEvent) -> None:
        data = {
            "plugin_id": self.plugin_id,
            "published_event": published_event,
        }
        await self._dispatcher.atrigger("plugin.consume", "before", data)
        try:
            await super()._consume(published_event)
        except Exception as error:
            await self._dispatcher.atrigger(
                "plugin.consume",
                "error",
                {**data, **self._error_data(error)},
            )
            raise
        await self._dispatcher.atrigger("plugin.consume", "after", data)

    async def _lifecycle(self, action: str, operation) -> None:
        data = {"plugin_id": self.plugin_id, "action": action}
        await self._dispatcher.atrigger("plugin.lifecycle", "before", data)
        try:
            await operation()
        except Exception as error:
            await self._dispatcher.atrigger(
                "plugin.lifecycle",
                "error",
                {**data, **self._error_data(error)},
            )
            raise
        await self._dispatcher.atrigger("plugin.lifecycle", "after", data)

    @staticmethod
    def _error_data(error: Exception) -> dict[str, str]:
        return {
            "error_type": type(error).__name__,
            "error_message": str(error),
        }
