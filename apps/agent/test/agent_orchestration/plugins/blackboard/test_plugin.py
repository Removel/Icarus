import asyncio

import pytest

from apps.agent.src.agent_orchestration.capability import (
    AgentCompletedEvent,
    AgentResponse,
)
from apps.agent.src.agent_orchestration.events import Event
from apps.agent.src.agent_orchestration.plugin_runtime import BasePlugin, PluginManager
from apps.agent.src.agent_orchestration.plugins import (
    BlackboardContextReadyEvent,
    BlackboardPlugin,
    ContextBlock,
    ContextContributionEvent,
    UserInputEvent,
)
from apps.agent.src.model_provider.types import Message, TextPart


class ProducerPlugin(BasePlugin):
    async def consume(self, source_plugin_id: str, event: Event) -> None:
        pass


class SinkPlugin(BasePlugin):
    def __init__(self, plugin_id: str) -> None:
        super().__init__(plugin_id)
        self.events = []

    async def consume(self, source_plugin_id: str, event: Event) -> None:
        self.events.append((source_plugin_id, event))


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
                correlation_id="task-1",
                prompt="hello",
            )
        )
        await memory.publish(
            ContextContributionEvent(
                correlation_id="task-1",
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
                correlation_id="task-1",
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
    assert [block.content for block in context.context_blocks] == [
        "memory-value"
    ]
    assert blackboard.get_task_state("task-1").agent_status == "running"


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
                correlation_id="task-1",
                status="failed",
                error="timeout",
            )
        )
        await user_input.publish(
            UserInputEvent(
                correlation_id="task-1",
                prompt="hello",
            )
        )
        await manager.stop(timeout=1)
        return agent.events

    events = asyncio.run(run())

    assert len(events) == 1
    context = events[0][1]
    assert context.context_errors == {"memory": "timeout"}


def test_blackboard_plugin_消费agent结果更新任务状态():
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
                correlation_id="task-1",
                prompt="hello",
            ),
        )
        await blackboard.consume(
            "agent",
            AgentCompletedEvent(
                correlation_id="task-1",
                step=1,
                response=AgentResponse(
                    message=Message("assistant", [TextPart("done")]),
                    finish_reason="stop",
                    steps=1,
                ),
            ),
        )
        return blackboard, published

    blackboard, published = asyncio.run(run())

    assert len(published) == 1
    state = blackboard.get_task_state("task-1")
    assert state.agent_status == "completed"
    assert state.agent_response.message.content == [TextPart("done")]


def test_blackboard_plugin_拒绝伪造context来源():
    async def run():
        blackboard = BlackboardPlugin(
            "blackboard",
            required_context_sources={"memory"},
        )
        await blackboard.consume(
            "memory",
            ContextContributionEvent(
                correlation_id="task-1",
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
