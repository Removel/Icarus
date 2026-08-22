import asyncio
from contextlib import contextmanager

import pytest

from apps.agent.src.agent_orchestration.capability import (
    AgentCompletedEvent,
    AgentErrorEvent,
    AgentResponse,
)
from apps.agent.src.agent_orchestration.events import Event
from apps.agent.src.agent_orchestration.plugin_runtime import BasePlugin, PluginManager
from apps.agent.src.agent_orchestration.plugins import (
    BlackboardContextReadyEvent,
    BlackboardPlugin,
    ContextBlock,
    ContextContributionEvent,
    InputFinishedEvent,
    UserInputEvent,
)
from apps.agent.src.model_provider.types import Message, TextPart


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

    assert len(events) == 1
    context = events[0][1]
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
            AgentErrorEvent(
                task_id="task-1",
                step=1,
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
