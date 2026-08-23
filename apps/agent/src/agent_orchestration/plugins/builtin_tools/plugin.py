from apps.agent.src.agent_orchestration.events import Event
from apps.agent.src.agent_orchestration.plugin_runtime import BasePlugin


class BuiltinToolsPlugin(BasePlugin):
    async def consume(self, source_plugin_id: str, event: Event) -> None:
        del source_plugin_id, event
