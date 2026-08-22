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
    AgentPlugin,
    BlackboardContextReadyEvent,
)
from apps.agent.src.model_provider.types import Message, TextPart


class StubAgent(BaseAgent):
    def __init__(self) -> None:
        self.calls = []

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
        self.calls.append(
            {
                "system_prompt": system_prompt,
                "history_messages": history_messages,
                "input_prompt": input_prompt,
                "input_images": input_images,
                "tools": tools,
            }
        )
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

        unrelated = Event(task_id="ignored")
        await blackboard.publish(unrelated)
        await blackboard.publish(
            BlackboardContextReadyEvent(
                task_id="task-1",
                model_role="thinking",
                system_prompt="system",
                input_prompt=(
                    "<plugin_context>\ncomposed context\n</plugin_context>"
                    "\n\n<user_request>\nhello\n</user_request>"
                ),
                tools=[],
            )
        )
        await manager.stop(timeout=1)
        return factory, sink.sources, sink.events

    factory, sources, events = asyncio.run(run())

    assert factory.roles == ["thinking"]
    assert sources == ["agent", "agent"]
    assert factory.agent.calls[0]["system_prompt"] == "system"
    assert "<plugin_context>" in factory.agent.calls[0]["input_prompt"]
    assert "composed context" in factory.agent.calls[0]["input_prompt"]
    assert "<user_request>\nhello\n</user_request>" in factory.agent.calls[0][
        "input_prompt"
    ]
    assert [type(event) for event in events] == [
        AgentTextDeltaEvent,
        AgentCompletedEvent,
    ]
    assert {event.task_id for event in events} == {"task-1"}
    assert events[0].text == "hello"
    assert events[1].response.message.content == [TextPart("hello")]


def test_blackboard_context_event_保持扁平agent参数():
    event = BlackboardContextReadyEvent(
        task_id="task-1",
        model_role="perception",
        system_prompt="system",
        history_messages=[Message("user", [TextPart("history")])],
        input_prompt="composed-input",
        tools=["read"],
    )

    assert event.model_role == "perception"
    assert event.system_prompt == "system"
    assert event.history_messages[0].role == "user"
    assert event.input_prompt == "composed-input"
    assert event.input_images == []
    assert event.tools == ["read"]
