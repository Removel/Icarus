import asyncio
import json
import logging

from agent_app.assembly import AgentFactory
from icarus.core.agents import (
    AgentCompletedEvent,
    AgentMessageCompletedEvent,
    AgentTextDeltaEvent,
)
from icarus.core.events import Event
from icarus.core.hooks import HookDispatcher, HookRegistry
from icarus.runtime import BasePlugin, PluginManager
from agent_app.plugins.agent.plugin import AgentPlugin
from agent_app.plugins.blackboard.plugin import BlackboardPlugin
from icarus.core.contracts.input import UserInputEvent
from agent_app.plugins.persistence import PersistenceRuntime
from icarus.runtime.recording import RuntimeRecorder
from agent_app.plugins.persistence.recording import FileRecordingBackend
from icarus.core.run_control import TaskChannelRegistry
from icarus.core.llm import BaseLLM
from icarus.core.models.messages import LLMStreamChunk


class StubLLM(BaseLLM):
    def invoke(self, messages, tools=None):
        raise NotImplementedError

    async def ainvoke(self, messages, tools=None):
        raise NotImplementedError

    def stream(self, messages, tools=None):
        return iter(())

    async def astream(self, messages, tools=None):
        yield LLMStreamChunk(text_delta="trace")
        yield LLMStreamChunk(text_delta="-ok", finish_reason="stop")

    def close(self):
        pass

    async def aclose(self):
        pass


class StubLLMFactory:
    def create_llm(self, role):
        return StubLLM()


class ProducerPlugin(BasePlugin):
    async def consume(self, source_plugin_id: str, event: Event) -> None:
        pass


class SinkPlugin(BasePlugin):
    def __init__(self, plugin_id: str) -> None:
        super().__init__(plugin_id)
        self.events = []

    async def consume(self, source_plugin_id: str, event: Event) -> None:
        self.events.append(event)


def test_persistence_记录完整agent和plugin_hook链路(tmp_path):
    async def run():
        hook_registry = HookRegistry()
        persistence = PersistenceRuntime(
            data_dir=tmp_path / "data",
            workspace_path=tmp_path / "workspace",
        )
        logger = logging.getLogger("trace-integration")
        persistence.start(hook_registry, logger)
        recorder = RuntimeRecorder()
        recorder.register_backend(FileRecordingBackend(persistence.trace_writer, persistence.redactor))
        recorder.start()
        hook_registry.recorder = recorder

        factory = AgentFactory(
            llm_factory=StubLLMFactory(),
            hook_registry=hook_registry,
            register_builtin_tools=False,
        )
        manager = PluginManager(
            hook_dispatcher=HookDispatcher(hook_registry),
        )
        user_input = ProducerPlugin("user-input")
        blackboard = BlackboardPlugin(
            "blackboard",
            required_context_sources=set(),
            model_role="thinking",
            system_prompt="system",
            tools=[],
        )
        task_channels = TaskChannelRegistry()
        channel = task_channels.create("task-1")
        channel.mark_preparing_context()
        agent = AgentPlugin("agent", factory, task_channels)
        sink = SinkPlugin("sink")
        for plugin in (user_input, blackboard, agent, sink):
            manager.register(plugin)
        manager.subscribe("blackboard", "user-input")
        manager.subscribe("agent", "blackboard")
        manager.subscribe("sink", "agent")
        await manager.start()

        with persistence.session_scope(
            session_id="session-1",
            task_id="task-1",
        ) as identity:
            await user_input.publish(
                UserInputEvent(
                    task_id="task-1",
                    prompt="hello",
                )
            )
            await manager.stop(timeout=2)

        await factory.aclose()
        recorder.stop()
        persistence.stop(drain=True, logger=logger)
        return persistence, identity, sink.events

    persistence, identity, events = asyncio.run(run())

    records = [
        json.loads(line)
        for line in persistence.resolver.trace_file(identity).read_text(
            encoding="utf-8"
        ).splitlines()
    ]
    names = {record["name"] for record in records}
    assert {
        "event.publish",
        "event.route",
        "plugin.consume",
        "agent.stream",
        "llm.stream",
    } <= names
    assert {record["task_id"] for record in records} == {"task-1"}
    assert all("workspace_key" not in record for record in records)
    assert all("session_id" not in record for record in records)
    assert [type(event) for event in events] == [
        AgentTextDeltaEvent,
        AgentTextDeltaEvent,
        AgentMessageCompletedEvent,
        AgentCompletedEvent,
    ]
    event_flow_records = [
        record
        for record in records
        if record["name"] in {
            "event.publish",
            "event.route",
            "plugin.consume",
        }
    ]
    assert all(
        '"text":"trace"' not in json.dumps(record, separators=(",", ":"))
        and '"text":"-ok"'
        not in json.dumps(record, separators=(",", ":"))
        for record in event_flow_records
    )
