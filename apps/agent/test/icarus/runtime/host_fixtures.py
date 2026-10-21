from typing import Any

from icarus.core.events import Event
from icarus.core.plugin import BasePlugin, handles
from icarus.core.registration import PluginRegistration, ProvidedCapability
from icarus.core.capabilities.catalog import CapabilityContract
from icarus.core.tools import BaseTool, ToolExecutionResult
from icarus.core.models.messages import ToolDefinition
from apps.agent.test.icarus.runtime.support import SampleEvent


class SampleAccess(CapabilityContract):
    capability_id = "sample-api"


class FixturePlugin(BasePlugin):
    def __init__(self, plugin_id: str) -> None:
        super().__init__(plugin_id)
        self.events: list[Event] = []


class FixtureConsumer(FixturePlugin):
    @handles(SampleEvent)
    async def on_sample(self, source_plugin_id, event):
        self.events.append(event)


class FixtureProducer(FixturePlugin):
    provided_capabilities = (SampleAccess,)

    def build_tools(self):
        return (FixtureTool(),)


class FixtureTool(BaseTool):
    @property
    def definition(self):
        return ToolDefinition("fixture-tool", "Fixture Tool", {
            "type": "object", "properties": {}, "additionalProperties": False,
        })

    def invoke(self, arguments: dict[str, Any], **execution):
        return ToolExecutionResult(success=True, output={"execution": execution})


def create_plugin(plugin_id, workspace_path, session_id, config, required_capabilities, logger):
    if plugin_id == "producer":
        return PluginRegistration(plugin=FixtureProducer(plugin_id), capabilities=(ProvidedCapability("sample-api", "1.0.0", object()),))
    if plugin_id == "consumer":
        return PluginRegistration(plugin=FixtureConsumer(plugin_id))
    return PluginRegistration(plugin=FixturePlugin(plugin_id))


def fail_plugin(**kwargs):
    raise RuntimeError("factory boom")
