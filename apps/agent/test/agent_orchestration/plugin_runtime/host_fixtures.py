from collections.abc import Mapping
from typing import Any

from apps.agent.src.agent_orchestration.events import Event
from apps.agent.src.agent_orchestration.plugin_runtime.base_plugin import BasePlugin
from apps.agent.src.agent_orchestration.plugin_runtime.registration import (
    PluginRegistration,
    ProvidedCapability,
)
from apps.agent.src.agent_orchestration.tools import BaseTool, ToolExecutionResult
from apps.agent.src.model_provider.types import ToolDefinition


class FixturePlugin(BasePlugin):
    def __init__(self, plugin_id: str) -> None:
        super().__init__(plugin_id)
        self.events: list[Event] = []

    async def consume(self, source_plugin_id: str, event: Event) -> None:
        del source_plugin_id
        self.events.append(event)


class FixtureTool(BaseTool):
    @property
    def definition(self):
        return ToolDefinition(
            name="fixture-tool",
            description="Fixture Tool",
            input_schema={
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        )

    def invoke(self, arguments: dict[str, Any], **execution):
        return ToolExecutionResult(
            success=True, output={"execution": execution}
        )


def create_plugin(
    plugin_id, workspace_path, session_id, config, required_capabilities,
    logger,
):
    del workspace_path, session_id, config, logger
    capabilities = ()
    tools = ()
    if plugin_id == "producer":
        capabilities = (
            ProvidedCapability("sample-api", "1.0.0", object()),
        )
        tools = (FixtureTool(),)
    if plugin_id == "consumer":
        assert ("producer", "sample-api") in required_capabilities
    return PluginRegistration(
        plugin=FixturePlugin(plugin_id),
        capabilities=capabilities,
        tools=tools,
    )


def fail_plugin(**kwargs):
    del kwargs
    raise RuntimeError("factory boom")
