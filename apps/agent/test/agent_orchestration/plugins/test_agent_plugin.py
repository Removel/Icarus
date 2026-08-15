import asyncio

from apps.agent.src.agent_orchestration.capability import (
    AgentCompletedEvent,
    AgentResponse,
    AgentTextDeltaEvent,
)
from apps.agent.src.agent_orchestration.capability.base_agent import BaseAgent
from apps.agent.src.agent_orchestration.events import Event
from apps.agent.src.agent_orchestration.plugin_runtime import BasePlugin, PluginManager
from apps.agent.src.agent_orchestration.plugins import (
    AgentContextReadyEvent,
    AgentPlugin,
)
from apps.agent.src.model_provider.types import Message, TextPart


class StubAgent(BaseAgent):
    @property
    def model_role(self):
        return "thinking"

    def invoke(self, *args, **kwargs):
        raise NotImplementedError

    async def ainvoke(self, *args, **kwargs):
        raise NotImplementedError

    def stream(self, *args, **kwargs):
        raise NotImplementedError

    async def astream(
        self,
        system_prompt,
        history_messages,
        input_prompt,
        input_images=None,
        tools=None,
    ):
        yield AgentTextDeltaEvent(step=1, text="hello")
        yield AgentCompletedEvent(
            step=1,
            response=AgentResponse(
                message=Message("assistant", [TextPart("hello")]),
                finish_reason="stop",
                steps=1,
            ),
        )


class StubAgentFactory:
    def __init__(self) -> None:
        self.agent = StubAgent()
        self.roles = []

    def get_agent(self, model_role):
        self.roles.append(model_role)
        return self.agent


class SinkPlugin(BasePlugin):
    def __init__(self, plugin_id: str) -> None:
        super().__init__(plugin_id)
        self.sources = []
        self.events = []

    async def consume(self, source_plugin_id: str, event: Event) -> None:
        self.sources.append(source_plugin_id)
        self.events.append(event)


def test_agent_plugin_只消费context并原样发布stream_event():
    async def run():
        manager = PluginManager()
        blackboard = SinkPlugin("blackboard")
        factory = StubAgentFactory()
        agent_plugin = AgentPlugin("agent", factory)
        sink = SinkPlugin("sink")
        for plugin in (blackboard, agent_plugin, sink):
            manager.register(plugin)
        manager.subscribe("agent", "blackboard")
        manager.subscribe("sink", "agent")
        await manager.start()

        unrelated = Event(correlation_id="ignored")
        await blackboard.publish(unrelated)
        await blackboard.publish(
            AgentContextReadyEvent(
                correlation_id="task-1",
                model_role="thinking",
                system_prompt="system",
                input_prompt="hello",
                tools=[],
            )
        )
        await manager.stop(timeout=1)
        return factory, sink.sources, sink.events

    factory, sources, events = asyncio.run(run())

    assert factory.roles == ["thinking"]
    assert sources == ["agent", "agent"]
    assert [type(event) for event in events] == [
        AgentTextDeltaEvent,
        AgentCompletedEvent,
    ]
    assert events[0].text == "hello"
    assert events[1].response.message.content == [TextPart("hello")]


def test_agent_context_event_保持扁平agent参数():
    event = AgentContextReadyEvent(
        correlation_id="task-1",
        model_role="perception",
        system_prompt="system",
        history_messages=[Message("user", [TextPart("history")])],
        input_prompt="input",
        tools=["read"],
    )

    assert event.model_role == "perception"
    assert event.system_prompt == "system"
    assert event.history_messages[0].role == "user"
    assert event.input_prompt == "input"
    assert event.input_images == []
    assert event.tools == ["read"]
