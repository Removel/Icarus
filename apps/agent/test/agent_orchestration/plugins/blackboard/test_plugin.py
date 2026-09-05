import asyncio
from contextlib import contextmanager

import pytest

from apps.agent.src.agent_orchestration.capability import (
    AgentCancelledEvent,
    AgentCompletedEvent,
    AgentResponse,
)
from apps.agent.src.agent_orchestration.events import Event, TaskErrorEvent
from apps.agent.src.agent_orchestration.plugin_runtime import BasePlugin, PluginManager
from apps.agent.src.agent_orchestration.plugins import (
    BlackboardCompactedEvent,
    BlackboardContextReadyEvent,
    BlackboardPlugin,
    ContextBlock,
    ContextContributionEvent,
    InputFinishedEvent,
    UserInputEvent,
)
from apps.agent.src.model_provider.types import (
    ImagePart,
    Message,
    TextPart,
    ToolCall,
    Usage,
)


class ProducerPlugin(BasePlugin):
    async def consume(self, source_plugin_id: str, event: Event) -> None:
        pass


class SessionStub:
    @contextmanager
    def task_scope(self, task_id: str):
        yield


class SinkPlugin(BasePlugin):
    def __init__(self, plugin_id: str) -> None:
        super().__init__(plugin_id)
        self.events = []

    async def consume(self, source_plugin_id: str, event: Event) -> None:
        self.events.append((source_plugin_id, event))


class ReplyAgentPlugin(BasePlugin):
    def __init__(self, plugin_id: str) -> None:
        super().__init__(plugin_id)
        self.contexts = []

    async def consume(self, source_plugin_id: str, event: Event) -> None:
        if not isinstance(event, BlackboardContextReadyEvent):
            return
        self.contexts.append(event)
        user_prompt = event.input_prompt.split(
            "<user_request>\n", 1
        )[1].split("\n</user_request>", 1)[0]
        await self.publish(
            AgentCompletedEvent(
                task_id=event.task_id,
                step=1,
                response=AgentResponse(
                    message=Message(
                        "assistant",
                        [TextPart(f"answer:{user_prompt}")],
                    ),
                    finish_reason="stop",
                    steps=1,
                ),
            )
        )


class CompactorStub:
    def __init__(self, *, error=None):
        self.error = error
        self.calls = []
        self.closed = False

    async def compact(self, messages):
        self.calls.append(messages)
        if self.error is not None:
            raise self.error
        return (
            Message(
                "user",
                [TextPart("<conversation_summary>\nsummary\n</conversation_summary>")],
            ),
            Usage(100, 20),
        )

    async def aclose(self):
        self.closed = True


def test_blackboard达到阈值只压缩旧历史再发布当前输入():
    async def run():
        old = Message("user", [TextPart("old history")])
        compactor = CompactorStub()
        plugin = BlackboardPlugin(
            "blackboard",
            required_context_sources=set(),
            initial_messages=[old],
            context_window=100,
            history_compactor=compactor,
        )
        plugin._context_tokens = 85
        events = []

        async def publish(event):
            events.append(event)

        plugin.bind_publisher(publish)
        await plugin.consume(
            "user-input",
            UserInputEvent(task_id="task-1", prompt="new input"),
        )
        return plugin, compactor, events

    plugin, compactor, events = asyncio.run(run())

    assert compactor.calls == [[Message("user", [TextPart("old history")])]]
    assert isinstance(events[0], BlackboardCompactedEvent)
    assert events[0].before_tokens == 85
    assert events[0].after_tokens == 20
    assert isinstance(events[1], BlackboardContextReadyEvent)
    assert events[1].history_messages == plugin.get_messages()
    assert "new input" not in repr(compactor.calls[0])
    assert plugin.context_tokens == 20


def test_blackboard低于阈值不压缩():
    async def run():
        compactor = CompactorStub()
        plugin = BlackboardPlugin(
            "blackboard",
            required_context_sources=set(),
            initial_messages=[Message("user", [TextPart("old")])],
            context_window=100,
            history_compactor=compactor,
        )
        plugin._context_tokens = 84
        events = []

        async def publish(event):
            events.append(event)

        plugin.bind_publisher(publish)
        await plugin.consume(
            "user-input",
            UserInputEvent(task_id="task-1", prompt="new"),
        )
        return compactor, events

    compactor, events = asyncio.run(run())
    assert compactor.calls == []
    assert len(events) == 1
    assert isinstance(events[0], BlackboardContextReadyEvent)


def test_blackboard拒绝自己发布的事件避免来源路由回环():
    plugin = BlackboardPlugin("blackboard", required_context_sources=set())

    assert plugin.accepts_event(
        "blackboard",
        BlackboardCompactedEvent(
            task_id="task-1", before_tokens=90, after_tokens=10
        ),
    ) is False
    assert plugin.accepts_event(
        "blackboard",
        TaskErrorEvent(
            task_id="task-1",
            fatal=True,
            code="compact_failed",
            error_type="RuntimeError",
            error_message="failed",
        ),
    ) is False


def test_blackboard_compact失败保留历史且不发布context():
    async def run():
        old = Message("user", [TextPart("old history")])
        plugin = BlackboardPlugin(
            "blackboard",
            required_context_sources=set(),
            initial_messages=[old],
            context_window=100,
            history_compactor=CompactorStub(error=RuntimeError("failed")),
        )
        plugin._context_tokens = 90
        events = []

        async def publish(event):
            events.append(event)

        plugin.bind_publisher(publish)
        await plugin.consume(
            "user-input",
            UserInputEvent(task_id="task-1", prompt="new input"),
        )
        return plugin, events

    plugin, events = asyncio.run(run())

    assert plugin.get_messages() == [Message("user", [TextPart("old history")])]
    assert plugin.context_tokens == 90
    assert len(events) == 1
    assert isinstance(events[0], TaskErrorEvent)
    assert events[0].fatal is True
    assert events[0].code == "compact_failed"


def test_blackboard成功提交使用last_usage更新上下文标记():
    async def run():
        plugin = BlackboardPlugin(
            "blackboard",
            required_context_sources=set(),
            context_window=100,
        )
        events = []

        async def publish(event):
            events.append(event)

        plugin.bind_publisher(publish)
        await plugin.consume(
            "user-input",
            UserInputEvent(task_id="task-1", prompt="hello"),
        )
        await plugin.consume(
            "agent",
            AgentCompletedEvent(
                task_id="task-1",
                step=1,
                response=AgentResponse(
                    message=Message("assistant", [TextPart("done")]),
                    last_usage=Usage(70, 10),
                    messages=[
                        Message("user", [TextPart("hello")]),
                        Message("assistant", [TextPart("done")]),
                    ],
                    task_message_start=0,
                ),
            ),
        )
        return plugin, events

    plugin, events = asyncio.run(run())

    assert plugin.context_tokens == 80
    assert not any(
        isinstance(event, TaskErrorEvent)
        and event.code == "usage_unavailable"
        for event in events
    )


def test_blackboard超长输入不启动agent():
    async def run():
        plugin = BlackboardPlugin(
            "blackboard",
            required_context_sources=set(),
            context_window=2,
        )
        events = []

        async def publish(event):
            events.append(event)

        plugin.bind_publisher(publish)
        await plugin.consume(
            "user-input",
            UserInputEvent(task_id="task-1", prompt="12345678"),
        )
        return events

    events = asyncio.run(run())
    assert len(events) == 1
    assert isinstance(events[0], TaskErrorEvent)
    assert events[0].code == "input_too_long"
    assert events[0].fatal is True


def test_blackboard旧状态恢复时context_tokens默认为未知():
    async def run():
        plugin = BlackboardPlugin("blackboard", required_context_sources=set())
        await plugin.restore_session_state(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": [{"type": "text", "text": "old"}],
                        "tool_calls": [],
                        "tool_call_id": None,
                    }
                ]
            },
            state_version=1,
        )
        return plugin, await plugin.snapshot_session_state()

    plugin, snapshot = asyncio.run(run())
    assert plugin.context_tokens is None
    assert snapshot["context_tokens"] is None
    assert plugin.get_messages() == [Message("user", [TextPart("old")])]


def test_blackboard图片状态兼容旧url并写入稳定source字段():
    async def run():
        plugin = BlackboardPlugin("blackboard", required_context_sources=set())
        await plugin.restore_session_state(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image",
                                "url": "https://example.com/old.png",
                                "media_type": "image/png",
                            }
                        ],
                        "tool_calls": [],
                        "tool_call_id": None,
                    }
                ]
            },
            state_version=1,
        )
        return plugin, await plugin.snapshot_session_state()

    plugin, snapshot = asyncio.run(run())

    assert plugin.get_messages()[0].content == [
        ImagePart("https://example.com/old.png", media_type="image/png")
    ]
    assert snapshot["messages"][0]["content"][0] == {
        "type": "image",
        "source": "https://example.com/old.png",
        "source_type": "url",
        "media_type": "image/png",
    }


def test_blackboard_tool结果图片保持call关联并可恢复():
    image = ImagePart("assets/tool.png", "asset", "image/png")

    async def run():
        plugin = BlackboardPlugin("blackboard", required_context_sources=set())
        await plugin.restore_session_state(
            {
                "messages": [
                    {
                        "role": "tool",
                        "content": [
                            {"type": "text", "text": "captured"},
                            {
                                "type": "image",
                                "source": image.source,
                                "source_type": image.source_type,
                                "media_type": image.media_type,
                            },
                        ],
                        "tool_calls": [],
                        "tool_call_id": "call-image",
                    }
                ]
            },
            state_version=1,
        )
        return plugin.get_messages()[0], await plugin.snapshot_session_state()

    message, snapshot = asyncio.run(run())
    assert message.role == "tool"
    assert message.tool_call_id == "call-image"
    assert message.content == [TextPart("captured"), image]
    assert snapshot["messages"][0]["tool_call_id"] == "call-image"


def test_blackboard_plugin_等待固定来源并只发布一次context():
    async def run():
        manager = PluginManager()
        user_input = ProducerPlugin("user-input")
        memory = ProducerPlugin("memory")
        skill = ProducerPlugin("skill")
        blackboard = BlackboardPlugin(
            "blackboard",
            required_context_sources={"memory", "skill"},
            model_role="thinking",
            system_prompt="stable-system",
        )
        agent = SinkPlugin("agent")
        for plugin in (user_input, memory, skill, blackboard, agent):
            manager.register(plugin)
        manager.subscribe("blackboard", "user-input")
        manager.subscribe("blackboard", "memory")
        manager.subscribe("blackboard", "skill")
        manager.subscribe("agent", "blackboard")
        await manager.start()

        await user_input.publish(
            UserInputEvent(
                task_id="task-1",
                prompt="hello",
            )
        )
        await memory.publish(
            ContextContributionEvent(
                task_id="task-1",
                status="completed",
                context_blocks=[
                    ContextBlock(
                        source_plugin_id="memory",
                        context_type="memory",
                        content="memory-value",
                    )
                ],
            )
        )
        await skill.publish(
            ContextContributionEvent(
                task_id="task-1",
                status="completed",
                context_blocks=[],
            )
        )
        await manager.stop(timeout=1)
        return blackboard, agent.events

    blackboard, events = asyncio.run(run())

    assert len(events) == 1
    source, context = events[0]
    assert source == "blackboard"
    assert isinstance(context, BlackboardContextReadyEvent)
    assert "memory-value" in context.input_prompt
    assert context.input_prompt.endswith(
        "<user_request>\nhello\n</user_request>"
    )
    task_state = blackboard.get_task_state("task-1")
    assert task_state.context_published is True
    assert task_state.input_prompt == context.input_prompt


def test_blackboard_plugin_失败来源计为完成并记录错误():
    async def run():
        manager = PluginManager()
        user_input = ProducerPlugin("user-input")
        memory = ProducerPlugin("memory")
        blackboard = BlackboardPlugin(
            "blackboard",
            required_context_sources={"memory"},
            model_role="thinking",
            system_prompt="stable-system",
        )
        agent = SinkPlugin("agent")
        for plugin in (user_input, memory, blackboard, agent):
            manager.register(plugin)
        manager.subscribe("blackboard", "user-input")
        manager.subscribe("blackboard", "memory")
        manager.subscribe("agent", "blackboard")
        await manager.start()

        await memory.publish(
            ContextContributionEvent(
                task_id="task-1",
                status="failed",
                error="timeout",
            )
        )
        await user_input.publish(
            UserInputEvent(
                task_id="task-1",
                prompt="hello",
            )
        )
        await manager.stop(timeout=1)
        return agent.events

    events = asyncio.run(run())

    assert len(events) == 2
    error = events[0][1]
    assert isinstance(error, TaskErrorEvent)
    assert error.code == "context_provider_failed"
    assert error.fatal is False
    context = events[1][1]
    assert context.input_prompt == (
        "<plugin_context_errors>\n"
        '{"memory":"timeout"}\n'
        "</plugin_context_errors>\n\n"
        "<user_request>\nhello\n</user_request>"
    )


def test_blackboard_plugin_成功历史复用发布给agent的完整input_prompt():
    async def run():
        blackboard = BlackboardPlugin(
            "blackboard",
            required_context_sources={"memory"},
            agent_plugin_id="agent",
        )
        published = []

        async def publish(event):
            published.append(event)

        blackboard.bind_publisher(publish)
        await blackboard.consume(
            "user-input",
            UserInputEvent(task_id="task-1", prompt="hello"),
        )
        await blackboard.consume(
            "memory",
            ContextContributionEvent(
                task_id="task-1",
                status="completed",
                context_blocks=[
                    ContextBlock(
                        source_plugin_id="memory",
                        context_type="memory",
                        content="remember me",
                    )
                ],
            ),
        )
        await blackboard.consume(
            "agent",
            AgentCompletedEvent(
                task_id="task-1",
                step=1,
                response=AgentResponse(
                    message=Message("assistant", [TextPart("done")]),
                    finish_reason="stop",
                    steps=1,
                ),
            ),
        )
        return blackboard, published[0]

    blackboard, context = asyncio.run(run())

    assert context.input_prompt is not None
    assert "remember me" in context.input_prompt
    assert blackboard.get_messages()[0] == Message(
        "user",
        [TextPart(context.input_prompt)],
    )
    assert blackboard.get_task_state("task-1").input_prompt == (
        context.input_prompt
    )


def test_blackboard_plugin成功时提交已应用运行中context():
    async def run():
        blackboard = BlackboardPlugin(
            "blackboard",
            required_context_sources=set(),
        )
        published = []

        async def publish(event):
            published.append(event)

        blackboard.bind_publisher(publish)
        await blackboard.consume(
            "user-input",
            UserInputEvent(task_id="task-1", prompt="original"),
        )
        context_message = Message(
            "user",
            [TextPart("<runtime_context>\n1. extra\n</runtime_context>")],
        )
        task_messages = [
            Message("user", [TextPart("<user_request>\noriginal\n</user_request>")]),
            context_message,
            Message("assistant", [TextPart("done")]),
        ]
        await blackboard.consume(
            "agent",
            AgentCompletedEvent(
                task_id="task-1",
                step=2,
                response=AgentResponse(
                    message=task_messages[-1],
                    messages=task_messages,
                    task_message_start=0,
                ),
            ),
        )
        return blackboard.get_messages()

    messages = asyncio.run(run())

    assert messages == [
        Message("user", [TextPart("<user_request>\noriginal\n</user_request>")]),
        Message("user", [TextPart("<runtime_context>\n1. extra\n</runtime_context>")]),
        Message("assistant", [TextPart("done")]),
    ]


def test_blackboard_plugin成功时提交当前task完整tool消息():
    async def run():
        blackboard = BlackboardPlugin("blackboard", required_context_sources=set())
        blackboard.bind_publisher(lambda event: asyncio.sleep(0))
        await blackboard.consume(
            "user-input",
            UserInputEvent(task_id="task-1", prompt="inspect"),
        )
        tool_call = ToolCall("call-1", "read", {"path": "settings.json"})
        full_messages = [
            Message("system", [TextPart("system")]),
            Message("user", [TextPart("old")]),
            Message("assistant", [TextPart("old answer")]),
            Message("user", [TextPart("<user_request>\ninspect\n</user_request>")]),
            Message("assistant", [], tool_calls=[tool_call]),
            Message("tool", [TextPart('{"success": true}')], tool_call_id="call-1"),
            Message("assistant", [TextPart("done")]),
        ]
        await blackboard.consume(
            "agent",
            AgentCompletedEvent(
                task_id="task-1",
                step=2,
                response=AgentResponse(
                    message=full_messages[-1],
                    messages=full_messages,
                    task_message_start=3,
                ),
            ),
        )
        return blackboard.get_messages()

    messages = asyncio.run(run())

    assert [message.role for message in messages] == [
        "user",
        "assistant",
        "tool",
        "assistant",
    ]
    assert messages[1].tool_calls[0].id == "call-1"
    assert messages[2].tool_call_id == "call-1"


def test_blackboard_plugin取消时提交一次安全消息前缀():
    async def run():
        blackboard = BlackboardPlugin("blackboard", required_context_sources=set())
        blackboard.bind_publisher(lambda event: asyncio.sleep(0))
        await blackboard.consume(
            "user-input",
            UserInputEvent(task_id="task-1", prompt="inspect"),
        )
        checkpoint = (
            Message("user", [TextPart("<user_request>\ninspect\n</user_request>")]),
            Message(
                "assistant",
                [],
                tool_calls=[ToolCall("call-1", "read", {"path": "a"})],
            ),
            Message("tool", [TextPart('{"success": true}')], tool_call_id="call-1"),
        )
        cancelled = AgentCancelledEvent(
            task_id="task-1",
            step=2,
            reason="user_requested",
            task_messages=checkpoint,
        )
        await blackboard.consume("agent", cancelled)
        await blackboard.consume("agent", cancelled)
        await blackboard.consume(
            "user-input",
            InputFinishedEvent(task_id="task-1", status="cancelled"),
        )
        return blackboard

    blackboard = asyncio.run(run())

    assert [message.role for message in blackboard.get_messages()] == [
        "user",
        "assistant",
        "tool",
    ]
    with pytest.raises(KeyError, match="not found"):
        blackboard.get_task_state("task-1")


def test_blackboard_plugin运行中取消时input终态先到仍等待安全历史():
    async def run():
        blackboard = BlackboardPlugin("blackboard", required_context_sources=set())
        blackboard.bind_publisher(lambda event: asyncio.sleep(0))
        await blackboard.consume(
            "user-input",
            UserInputEvent(task_id="task-1", prompt="inspect"),
        )
        await blackboard.consume(
            "user-input",
            InputFinishedEvent(
                task_id="task-1",
                status="cancelled",
                run_id="run-1",
            ),
        )
        state = blackboard.get_task_state("task-1")
        await blackboard.consume(
            "agent",
            AgentCancelledEvent(
                task_id="task-1",
                step=1,
                task_messages=(
                    Message(
                        "user",
                        [TextPart("<user_request>\ninspect\n</user_request>")],
                    ),
                ),
            ),
        )
        return blackboard, state

    blackboard, state_before_agent = asyncio.run(run())

    assert state_before_agent.input_finished is True
    assert blackboard.get_messages() == [
        Message("user", [TextPart("<user_request>\ninspect\n</user_request>")]),
    ]
    with pytest.raises(KeyError, match="not found"):
        blackboard.get_task_state("task-1")


def test_blackboard_plugin取消后的下一task使用安全历史():
    async def run():
        blackboard = BlackboardPlugin("blackboard", required_context_sources=set())
        published = []

        async def publish(event):
            published.append(event)

        blackboard.bind_publisher(publish)
        await blackboard.consume(
            "user-input",
            UserInputEvent(task_id="task-1", prompt="inspect"),
        )
        checkpoint = (
            Message("user", [TextPart("<user_request>\ninspect\n</user_request>")]),
            Message(
                "assistant",
                [],
                tool_calls=[ToolCall("call-1", "read", {"path": "a"})],
            ),
            Message("tool", [TextPart('{"success": true}')], tool_call_id="call-1"),
        )
        await blackboard.consume(
            "agent",
            AgentCancelledEvent(
                task_id="task-1",
                step=2,
                task_messages=checkpoint,
            ),
        )
        await blackboard.consume(
            "user-input",
            InputFinishedEvent(
                task_id="task-1",
                status="cancelled",
                run_id="run-1",
            ),
        )
        await blackboard.consume(
            "user-input",
            UserInputEvent(task_id="task-2", prompt="continue"),
        )
        return published, checkpoint

    published, checkpoint = asyncio.run(run())

    assert published[1].task_id == "task-2"
    assert published[1].history_messages == list(checkpoint)


def test_blackboard_plugin_消费agent结果更新跨轮消息并在任务完成后清理():
    async def run():
        blackboard = BlackboardPlugin(
            "blackboard",
            required_context_sources=set(),
            agent_plugin_id="agent",
            model_role="thinking",
            system_prompt="system",
        )
        published = []

        async def publish(event):
            published.append(event)

        blackboard.bind_publisher(publish)
        await blackboard.consume(
            "user-input",
            UserInputEvent(
                task_id="task-1",
                prompt="hello",
            ),
        )
        await blackboard.consume(
            "agent",
            AgentCompletedEvent(
                task_id="task-1",
                step=1,
                response=AgentResponse(
                    message=Message("assistant", [TextPart("done")]),
                    finish_reason="stop",
                    steps=1,
                ),
            ),
        )
        await blackboard.consume(
            "user-input",
            InputFinishedEvent(
                task_id="task-1",
                status="completed",
            ),
        )
        return blackboard, published

    blackboard, published = asyncio.run(run())

    assert len(published) == 1
    assert blackboard.get_messages() == [
        Message(
            "user",
            [TextPart("<user_request>\nhello\n</user_request>")],
        ),
        Message("assistant", [TextPart("done")]),
    ]
    with pytest.raises(KeyError, match="not found"):
        blackboard.get_task_state("task-1")


def test_blackboard_plugin_下一轮自动使用已完成消息作为history():
    async def run():
        blackboard = BlackboardPlugin(
            "blackboard",
            required_context_sources=set(),
            agent_plugin_id="agent",
            model_role="thinking",
            system_prompt="system",
        )
        published = []

        async def publish(event):
            published.append(event)

        blackboard.bind_publisher(publish)
        await blackboard.consume(
            "user-input",
            UserInputEvent(
                task_id="task-1",
                prompt="first",
            ),
        )
        await blackboard.consume(
            "agent",
            AgentCompletedEvent(
                task_id="task-1",
                step=1,
                response=AgentResponse(
                    message=Message("assistant", [TextPart("first-answer")]),
                    finish_reason="stop",
                    steps=1,
                ),
            ),
        )
        await blackboard.consume(
            "user-input",
            InputFinishedEvent(
                task_id="task-1",
                status="completed",
            ),
        )
        await blackboard.consume(
            "user-input",
            UserInputEvent(
                task_id="task-2",
                prompt="second",
            ),
        )
        return blackboard, published

    blackboard, published = asyncio.run(run())

    assert len(published) == 2
    second_context = published[1]
    assert second_context.history_messages == [
        Message(
            "user",
            [TextPart("<user_request>\nfirst\n</user_request>")],
        ),
        Message("assistant", [TextPart("first-answer")]),
    ]
    assert blackboard.get_task_state("task-2").context_published is True


def test_blackboard_plugin_初始化历史只复制一次并用于首轮context():
    async def run():
        initial_messages = [
            Message("user", [TextPart("restored-user")]),
            Message("assistant", [TextPart("restored-assistant")]),
        ]
        blackboard = BlackboardPlugin(
            "blackboard",
            required_context_sources=set(),
            initial_messages=initial_messages,
        )
        initial_messages.clear()
        published = []

        async def publish(event):
            published.append(event)

        blackboard.bind_publisher(publish)
        await blackboard.consume(
            "user-input",
            UserInputEvent(
                task_id="task-1",
                prompt="continue",
            ),
        )
        return blackboard, published

    blackboard, published = asyncio.run(run())

    assert published[0].history_messages == [
        Message("user", [TextPart("restored-user")]),
        Message("assistant", [TextPart("restored-assistant")]),
    ]
    assert blackboard.get_messages() == published[0].history_messages


def test_blackboard_plugin_失败任务不写入跨轮消息():
    async def run():
        blackboard = BlackboardPlugin(
            "blackboard",
            required_context_sources=set(),
        )
        blackboard.bind_publisher(lambda event: asyncio.sleep(0))
        await blackboard.consume(
            "user-input",
            UserInputEvent(
                task_id="task-1",
                prompt="failed",
            ),
        )
        await blackboard.consume(
            "user-input",
            InputFinishedEvent(
                task_id="task-1",
                status="failed",
            ),
        )
        await blackboard.consume(
            "agent",
            TaskErrorEvent(
                task_id="task-1",
                fatal=True,
                code="agent_run_failed",
                step=1,
                run_id="run-1",
                error_type="RuntimeError",
                error_message="failed",
            ),
        )
        return blackboard

    blackboard = asyncio.run(run())

    assert blackboard.get_messages() == []
    with pytest.raises(KeyError, match="not found"):
        blackboard.get_task_state("task-1")


def test_blackboard_plugin_input完成先到时等待agent结果再提交并清理():
    async def run():
        blackboard = BlackboardPlugin(
            "blackboard",
            required_context_sources=set(),
            agent_plugin_id="agent",
        )
        blackboard.bind_publisher(lambda event: asyncio.sleep(0))
        await blackboard.consume(
            "user-input",
            UserInputEvent(
                task_id="task-1",
                prompt="hello",
            ),
        )
        await blackboard.consume(
            "user-input",
            InputFinishedEvent(
                task_id="task-1",
                status="completed",
            ),
        )
        state_before_agent = blackboard.get_task_state("task-1")
        await blackboard.consume(
            "agent",
            AgentCompletedEvent(
                task_id="task-1",
                step=1,
                response=AgentResponse(
                    message=Message("assistant", [TextPart("done")]),
                    finish_reason="stop",
                    steps=1,
                ),
            ),
        )
        return blackboard, state_before_agent

    blackboard, state_before_agent = asyncio.run(run())

    assert state_before_agent.input_finished is True
    assert blackboard.get_messages() == [
        Message(
            "user",
            [TextPart("<user_request>\nhello\n</user_request>")],
        ),
        Message("assistant", [TextPart("done")]),
    ]
    with pytest.raises(KeyError, match="not found"):
        blackboard.get_task_state("task-1")


def test_blackboard_plugin_runtime连续任务自动传递跨轮history():
    async def run():
        from apps.agent.src.agent_orchestration.plugins import UserInputPlugin

        manager = PluginManager()
        user_input = UserInputPlugin("user-input", SessionStub())
        blackboard = BlackboardPlugin(
            "blackboard",
            required_context_sources=set(),
        )
        agent = ReplyAgentPlugin("agent")
        for plugin in (user_input, blackboard, agent):
            manager.register(plugin)
        manager.subscribe("blackboard", "user-input")
        manager.subscribe("agent", "blackboard")
        manager.subscribe("user-input", "agent")
        manager.subscribe("blackboard", "agent")
        await manager.start()

        first = await user_input.submit("first")
        second = await user_input.submit("second")
        await user_input.drain()
        await manager.stop(timeout=1)
        return blackboard, agent.contexts, first, second

    blackboard, contexts, first, second = asyncio.run(run())

    assert len(contexts) == 2
    assert contexts[0].history_messages == []
    assert contexts[1].history_messages == [
        Message(
            "user",
            [TextPart("<user_request>\nfirst\n</user_request>")],
        ),
        Message("assistant", [TextPart("answer:first")]),
    ]
    assert blackboard.get_messages() == [
        Message(
            "user",
            [TextPart("<user_request>\nfirst\n</user_request>")],
        ),
        Message("assistant", [TextPart("answer:first")]),
        Message(
            "user",
            [TextPart("<user_request>\nsecond\n</user_request>")],
        ),
        Message("assistant", [TextPart("answer:second")]),
    ]
    with pytest.raises(KeyError, match="not found"):
        blackboard.get_task_state(first.task_id)
    with pytest.raises(KeyError, match="not found"):
        blackboard.get_task_state(second.task_id)


def test_blackboard_plugin_拒绝伪造context来源():
    async def run():
        blackboard = BlackboardPlugin(
            "blackboard",
            required_context_sources={"memory"},
        )
        await blackboard.consume(
            "memory",
            ContextContributionEvent(
                task_id="task-1",
                status="completed",
                context_blocks=[
                    ContextBlock(
                        source_plugin_id="skill",
                        context_type="memory",
                        content="forged",
                    )
                ],
            ),
        )

    with pytest.raises(ValueError, match="does not match publisher"):
        asyncio.run(run())
