import asyncio

from apps.agent.src.agent_orchestration.capability import (
    AgentCancelledEvent,
    AgentCompletedEvent,
    AgentMessageCompletedEvent,
    AgentResponse,
    AgentTextDeltaEvent,
    AgentToolCompletedEvent,
)
from apps.agent.src.agent_orchestration.capability.base_agent import BaseAgent
from apps.agent.src.agent_orchestration.events import Event, TaskErrorEvent
from apps.agent.src.agent_orchestration.hooks import (
    BaseHook,
    HookDispatcher,
    HookRegistry,
)
from apps.agent.src.agent_orchestration.plugin_runtime import BasePlugin, PluginManager
from apps.agent.src.agent_orchestration.plugins import (
    AgentPlugin,
    BlackboardContextReadyEvent,
)
from apps.agent.src.agent_orchestration.run_control import TaskChannelRegistry
from apps.agent.src.agent_orchestration.run_control import (
    TaskCancelRequestedEvent,
    TaskCancelResultEvent,
    TaskContextInputEvent,
    TaskContextInputResultEvent,
)
from apps.agent.src.model_provider.types import (
    LLMResponse,
    Message,
    TextPart,
    ToolCall,
    Usage,
)
from apps.agent.src.agent_orchestration.tools import ToolExecutionResult


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
        run_control=None,
    ):
        self.calls.append(
            {
                "system_prompt": system_prompt,
                "history_messages": history_messages,
                "input_prompt": input_prompt,
                "input_images": input_images,
                "tools": tools,
                "run_control": run_control,
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
        self.closed = False

    def get_agent(self, model_role):
        self.roles.append(model_role)
        return self.agent

    async def aclose(self):
        self.closed = True


class SinkPlugin(BasePlugin):
    def __init__(self, plugin_id: str) -> None:
        super().__init__(plugin_id)
        self.sources = []
        self.events = []

    async def consume(self, source_plugin_id: str, event: Event) -> None:
        self.sources.append(source_plugin_id)
        self.events.append(event)


class RecordingHook(BaseHook):
    def __init__(self) -> None:
        self.events = []

    def handle(self, event) -> None:
        self.events.append(event)


def test_agent_plugin_只消费context并原样发布stream_event():
    async def run():
        manager = PluginManager()
        blackboard = SinkPlugin("blackboard")
        factory = StubAgentFactory()
        task_channels = TaskChannelRegistry()
        channel = task_channels.create("task-1")
        channel.mark_preparing_context()
        agent_plugin = AgentPlugin("agent", factory, task_channels)
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
    assert factory.closed is True
    assert sources == ["agent", "agent", "agent"]
    assert factory.agent.calls[0]["system_prompt"] == "system"
    assert "<plugin_context>" in factory.agent.calls[0]["input_prompt"]
    assert "composed context" in factory.agent.calls[0]["input_prompt"]
    assert "<user_request>\nhello\n</user_request>" in factory.agent.calls[0][
        "input_prompt"
    ]
    assert [type(event) for event in events] == [
        AgentTextDeltaEvent,
        AgentMessageCompletedEvent,
        AgentCompletedEvent,
    ]
    assert {event.task_id for event in events} == {"task-1"}
    assert events[0].text == "hello"
    assert events[1].message.content == [TextPart("hello")]
    assert events[2].response.message.content == [TextPart("hello")]


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


def test_agent_plugin运行中取消发布唯一cancelled终态():
    class BlockingAgent(StubAgent):
        def __init__(self):
            super().__init__()
            self.started = asyncio.Event()

        async def astream(self, *args, run_control=None, **kwargs):
            del args, kwargs
            self.started.set()
            await asyncio.Event().wait()
            if False:
                yield

    async def run():
        manager = PluginManager()
        blackboard = SinkPlugin("blackboard")
        factory = StubAgentFactory()
        factory.agent = BlockingAgent()
        channels = TaskChannelRegistry()
        channel = channels.create("task-1")
        channel.mark_preparing_context()
        agent_plugin = AgentPlugin("agent", factory, channels)
        sink = SinkPlugin("sink")
        for plugin in (blackboard, agent_plugin, sink):
            manager.register(plugin)
        manager.subscribe("agent", "blackboard")
        manager.subscribe("sink", "agent")
        await manager.start()

        await blackboard.publish(
            BlackboardContextReadyEvent(
                task_id="task-1",
                model_role="thinking",
                system_prompt="",
                input_prompt="work",
            )
        )
        await factory.agent.started.wait()
        channel.checkpoint_history(
            [Message("user", [TextPart("safe prefix")])]
        )
        first = agent_plugin.handle_task_operation(
            "external",
            TaskCancelRequestedEvent(
                task_id="task-1",
                reason="user_requested",
            ),
        )
        second = agent_plugin.handle_task_operation(
            "external",
            TaskCancelRequestedEvent(task_id="task-1", reason="again"),
        )
        await agent_plugin.drain()
        await manager.event_bus.drain()
        await sink.drain()
        events = list(sink.events)
        await manager.stop(timeout=1)
        return first, second, events, channel

    first, second, events, channel = asyncio.run(run())
    cancelled = [event for event in events if isinstance(event, AgentCancelledEvent)]

    assert first.status == "accepted"
    assert second.status in {"already_cancelling", "already_finished"}
    assert len(cancelled) == 1
    assert cancelled[0].reason == "user_requested"
    assert cancelled[0].task_messages == (
        Message("user", [TextPart("safe prefix")]),
    )
    assert channel.status.value == "cancelled"


def test_agent_plugin在run协程启动前取消仍发布cancelled终态():
    async def run():
        manager = PluginManager()
        factory = StubAgentFactory()
        channels = TaskChannelRegistry()
        channel = channels.create("task-1")
        channel.mark_preparing_context()
        agent_plugin = AgentPlugin("agent", factory, channels)
        sink = SinkPlugin("sink")
        for plugin in (agent_plugin, sink):
            manager.register(plugin)
        manager.subscribe("sink", "agent")
        await manager.start()

        await agent_plugin.consume(
            "blackboard",
            BlackboardContextReadyEvent(
                task_id="task-1",
                model_role="thinking",
                system_prompt="",
                input_prompt="work",
            ),
        )
        result = agent_plugin.handle_task_operation(
            "external",
            TaskCancelRequestedEvent(
                task_id="task-1",
                reason="cancel_before_start",
            ),
        )
        await agent_plugin.drain()
        await manager.stop(timeout=1)
        return result, factory, sink.events, channel

    result, factory, events, channel = asyncio.run(run())
    cancelled = [event for event in events if isinstance(event, AgentCancelledEvent)]

    assert result.status == "accepted"
    assert factory.roles == []
    assert len(cancelled) == 1
    assert cancelled[0].reason == "cancel_before_start"
    assert channel.status.value == "cancelled"


def test_agent_plugin仅为eventbus操作发布结果事件():
    async def run():
        manager = PluginManager()
        source = SinkPlugin("source")
        factory = StubAgentFactory()
        channels = TaskChannelRegistry()
        channel = channels.create("task-1")
        channel.mark_preparing_context()
        agent_plugin = AgentPlugin("agent", factory, channels)
        sink = SinkPlugin("sink")
        for plugin in (source, agent_plugin, sink):
            manager.register(plugin)
        manager.subscribe("agent", "source")
        manager.subscribe("sink", "agent")
        await manager.start()

        direct = agent_plugin.handle_task_operation(
            "service",
            TaskContextInputEvent(task_id="task-1", content="direct"),
        )
        await manager.event_bus.drain()
        await sink.drain()
        direct_events = list(sink.events)

        request = TaskContextInputEvent(
            task_id="task-1",
            content="from event",
        )
        await source.publish(request)
        await manager.stop(timeout=1)
        return direct, direct_events, request, sink.events

    direct, direct_events, request, events = asyncio.run(run())
    results = [
        event for event in events if isinstance(event, TaskContextInputResultEvent)
    ]

    assert direct.status == "accepted"
    assert direct_events == []
    assert len(results) == 1
    assert results[0].request_event_id == request.event_id
    assert results[0].status == "accepted"
    assert not any(isinstance(event, TaskCancelResultEvent) for event in events)


def test_agent_plugin记录操作结果和已应用context():
    registry = HookRegistry()
    recorder = RecordingHook()
    registry.register("*", recorder)
    channels = TaskChannelRegistry()
    channel = channels.create("task-1")
    channel.mark_preparing_context()
    channel.start_run("run-1")
    plugin = AgentPlugin(
        "agent",
        StubAgentFactory(),
        channels,
        hook_dispatcher=HookDispatcher(registry),
    )
    request = TaskContextInputEvent(task_id="task-1", content="extra")

    result = plugin.handle_task_operation("memory", request)
    batch = channel.drain_context(applied_before_step=2)
    assert batch is not None
    plugin._trace_applied_context(
        "task-1",
        channel,
    )

    assert result.status == "accepted"
    assert [event.name for event in recorder.events] == [
        "task.operation",
        "task.operation",
        "task.context",
    ]
    assert [event.phase for event in recorder.events] == [
        "before",
        "after",
        "applied",
    ]
    assert {event.run_id for event in recorder.events} == {"run-1"}
    assert recorder.events[1].data["status"] == "accepted"
    assert recorder.events[2].data["request_event_id"] == request.event_id


def test_agent_plugin为直接异常发布failed终态():
    class FailingAgent(StubAgent):
        async def astream(self, *args, **kwargs):
            del args, kwargs
            raise RuntimeError("agent exploded")
            if False:
                yield

    async def run():
        manager = PluginManager()
        blackboard = SinkPlugin("blackboard")
        factory = StubAgentFactory()
        factory.agent = FailingAgent()
        channels = TaskChannelRegistry()
        channel = channels.create("task-1")
        channel.mark_preparing_context()
        agent_plugin = AgentPlugin("agent", factory, channels)
        sink = SinkPlugin("sink")
        for plugin in (blackboard, agent_plugin, sink):
            manager.register(plugin)
        manager.subscribe("agent", "blackboard")
        manager.subscribe("sink", "agent")
        await manager.start()

        await blackboard.publish(
            BlackboardContextReadyEvent(
                task_id="task-1",
                model_role="thinking",
                system_prompt="",
                input_prompt="work",
            )
        )
        await manager.stop(timeout=1)
        events = list(sink.events)
        return events, channel

    events, channel = asyncio.run(run())
    errors = [event for event in events if isinstance(event, TaskErrorEvent)]

    assert len(errors) == 1
    assert errors[0].fatal is True
    assert errors[0].code == "agent_run_failed"
    assert errors[0].error_message == "agent exploded"
    assert channel.status.value == "failed"


def test_agent_plugin异常前已流出的文本发布为完整部分消息():
    class PartialFailingAgent(StubAgent):
        async def astream(self, *args, **kwargs):
            del args, kwargs
            yield AgentTextDeltaEvent(step=1, text="partial")
            raise RuntimeError("agent exploded")

    async def run():
        manager = PluginManager()
        factory = StubAgentFactory()
        factory.agent = PartialFailingAgent()
        channels = TaskChannelRegistry()
        channel = channels.create("task-1")
        channel.mark_preparing_context()
        agent_plugin = AgentPlugin("agent", factory, channels)
        sink = SinkPlugin("sink")
        for plugin in (agent_plugin, sink):
            manager.register(plugin)
        manager.subscribe("sink", "agent")
        await manager.start()
        await agent_plugin.consume(
            "blackboard",
            BlackboardContextReadyEvent(
                task_id="task-1",
                model_role="thinking",
                system_prompt="",
                input_prompt="work",
            ),
        )
        await agent_plugin.drain()
        await manager.event_bus.drain()
        await sink.drain()
        events = list(sink.events)
        await manager.stop(timeout=1)
        return events

    events = asyncio.run(run())
    assert [type(event) for event in events] == [
        AgentTextDeltaEvent,
        AgentMessageCompletedEvent,
        TaskErrorEvent,
    ]
    assert events[1].message.content == [TextPart("partial")]


def test_agent_plugin最终关闭后异常仍发布failed终态():
    class ErrorAfterCloseAgent(StubAgent):
        async def astream(self, *args, run_control=None, **kwargs):
            del args, kwargs
            assert run_control is not None
            run_control.close_or_drain(applied_before_step=1)
            raise RuntimeError("too late")
            if False:
                yield

    async def run():
        manager = PluginManager()
        factory = StubAgentFactory()
        factory.agent = ErrorAfterCloseAgent()
        channels = TaskChannelRegistry()
        channel = channels.create("task-1")
        channel.mark_preparing_context()
        agent_plugin = AgentPlugin("agent", factory, channels)
        sink = SinkPlugin("sink")
        for plugin in (agent_plugin, sink):
            manager.register(plugin)
        manager.subscribe("sink", "agent")
        await manager.start()

        await agent_plugin.consume(
            "blackboard",
            BlackboardContextReadyEvent(
                task_id="task-1",
                model_role="thinking",
                system_prompt="",
                input_prompt="work",
            ),
        )
        await agent_plugin.drain()
        await manager.stop(timeout=1)
        return sink.events, channel

    events, channel = asyncio.run(run())

    errors = [event for event in events if isinstance(event, TaskErrorEvent)]
    assert len(errors) == 1
    assert errors[0].error_message == "too late"
    assert channel.status.value == "failed"


def test_agent_plugin最大step错误携带安全检查点():
    class MaxStepAgent(StubAgent):
        async def astream(self, *args, run_control=None, **kwargs):
            del args, kwargs
            assert run_control is not None
            checkpoint = (
                Message("user", [TextPart("work")]),
                Message(
                    "assistant",
                    [],
                    tool_calls=[ToolCall("call-1", "read", {})],
                ),
                Message("tool", [TextPart("{}")], tool_call_id="call-1"),
            )
            run_control.checkpoint_history(checkpoint, Usage(10, 2))
            run_control.raise_if_step_exceeded(2)
            if False:
                yield

    async def run():
        manager = PluginManager()
        factory = StubAgentFactory()
        factory.agent = MaxStepAgent()
        channels = TaskChannelRegistry(max_steps=1)
        channel = channels.create("task-1")
        channel.mark_preparing_context()
        agent_plugin = AgentPlugin("agent", factory, channels)
        sink = SinkPlugin("sink")
        for plugin in (agent_plugin, sink):
            manager.register(plugin)
        manager.subscribe("sink", "agent")
        await manager.start()
        await agent_plugin.consume(
            "blackboard",
            BlackboardContextReadyEvent(
                task_id="task-1",
                model_role="thinking",
                system_prompt="",
                input_prompt="work",
            ),
        )
        await agent_plugin.drain()
        await manager.stop(timeout=1)
        return sink.events

    errors = [
        event
        for event in asyncio.run(run())
        if isinstance(event, TaskErrorEvent)
    ]
    assert len(errors) == 1
    assert errors[0].code == "max_steps_exceeded"
    assert errors[0].last_usage == Usage(10, 2)
    assert [message.role for message in errors[0].task_messages] == [
        "user",
        "assistant",
        "tool",
    ]


def test_agent_plugin工具失败发布非致命错误但run仍完成():
    class ToolFailureAgent(StubAgent):
        async def astream(self, *args, **kwargs):
            del args, kwargs
            call = ToolCall("call-1", "read", {})
            yield AgentToolCompletedEvent(
                step=1,
                tool_call=call,
                result=ToolExecutionResult(False, error="not found"),
            )
            yield AgentCompletedEvent(
                step=2,
                response=AgentResponse(
                    Message("assistant", [TextPart("recovered")])
                ),
            )

    async def run():
        manager = PluginManager()
        factory = StubAgentFactory()
        factory.agent = ToolFailureAgent()
        channels = TaskChannelRegistry()
        channel = channels.create("task-1")
        channel.mark_preparing_context()
        agent_plugin = AgentPlugin("agent", factory, channels)
        sink = SinkPlugin("sink")
        for plugin in (agent_plugin, sink):
            manager.register(plugin)
        manager.subscribe("sink", "agent")
        await manager.start()
        await agent_plugin.consume(
            "blackboard",
            BlackboardContextReadyEvent(
                task_id="task-1",
                model_role="thinking",
                system_prompt="",
                input_prompt="work",
            ),
        )
        await agent_plugin.drain()
        await manager.stop(timeout=1)
        return sink.events, channel

    events, channel = asyncio.run(run())
    errors = [event for event in events if isinstance(event, TaskErrorEvent)]
    assert len(errors) == 1
    assert errors[0].code == "tool_execution_failed"
    assert errors[0].fatal is False
    assert channel.status.value == "completed"
