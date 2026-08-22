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
    InputFinishedEvent,
    InputQueuedEvent,
    InputStartedEvent,
    UserInputEvent,
    UserInputPlugin,
)
from apps.agent.src.model_provider.types import Message, TextPart


class SessionStub:
    def __init__(self) -> None:
        self.task_ids = []

    @contextmanager
    def task_scope(self, task_id: str):
        self.task_ids.append(task_id)
        yield


class SinkPlugin(BasePlugin):
    def __init__(self, plugin_id: str) -> None:
        super().__init__(plugin_id)
        self.received = []

    async def consume(self, source_plugin_id: str, event: Event) -> None:
        self.received.append((source_plugin_id, event))


async def wait_until(predicate, timeout: float = 1) -> None:
    async def poll() -> None:
        while not predicate():
            await asyncio.sleep(0)

    await asyncio.wait_for(poll(), timeout=timeout)


def test_user_input_plugin_fifo并返回queue_position():
    async def run():
        manager = PluginManager()
        session = SessionStub()
        user_input = UserInputPlugin("user-input", session)
        blackboard = SinkPlugin("blackboard")
        backend = SinkPlugin("backend")
        agent = SinkPlugin("agent")
        for plugin in (user_input, blackboard, backend, agent):
            manager.register(plugin)
        manager.subscribe("blackboard", "user-input")
        manager.subscribe("backend", "user-input")
        manager.subscribe("user-input", "agent")
        await manager.start()

        first = await user_input.submit("first")
        second = await user_input.submit("second")
        await wait_until(
            lambda: any(
                isinstance(event, UserInputEvent)
                for _, event in blackboard.received
            )
        )

        queued = [
            event
            for _, event in backend.received
            if isinstance(event, InputQueuedEvent)
        ]
        started = [
            event
            for _, event in blackboard.received
            if isinstance(event, InputStartedEvent)
        ]
        user_events = [
            event
            for _, event in blackboard.received
            if isinstance(event, UserInputEvent)
        ]
        first_snapshot = (
            first,
            second,
            list(queued),
            list(started),
            list(user_events),
            list(session.task_ids),
        )

        await agent.publish(
            AgentCompletedEvent(
                task_id=first.task_id,
                step=1,
                response=AgentResponse(
                    message=Message("assistant", [TextPart("done")]),
                    finish_reason="stop",
                    steps=1,
                ),
            )
        )
        await wait_until(
            lambda: len(
                [
                    event
                    for _, event in blackboard.received
                    if isinstance(event, UserInputEvent)
                ]
            )
            == 2
        )
        await agent.publish(
            AgentCompletedEvent(
                task_id=second.task_id,
                step=1,
                response=AgentResponse(
                    message=Message("assistant", [TextPart("done")]),
                    finish_reason="stop",
                    steps=1,
                ),
            )
        )
        await manager.stop(timeout=1)
        return first_snapshot

    first, second, queued, started, user_events, task_ids = asyncio.run(run())

    assert first.queue_position == 0
    assert second.queue_position == 1
    assert [event.queue_position for event in queued] == [0, 1]
    assert [event.task_id for event in started] == [first.task_id]
    assert [event.prompt for event in user_events] == ["first"]
    assert task_ids[:3] == [first.task_id, second.task_id, first.task_id]


def test_user_input_plugin_完成或失败后开始下一条():
    async def run():
        manager = PluginManager()
        session = SessionStub()
        user_input = UserInputPlugin("user-input", session)
        blackboard = SinkPlugin("blackboard")
        backend = SinkPlugin("backend")
        agent = SinkPlugin("agent")
        for plugin in (user_input, blackboard, backend, agent):
            manager.register(plugin)
        manager.subscribe("blackboard", "user-input")
        manager.subscribe("backend", "user-input")
        manager.subscribe("user-input", "agent")
        await manager.start()
        first = await user_input.submit("first")
        second = await user_input.submit("second")
        await wait_until(
            lambda: any(
                isinstance(event, UserInputEvent)
                for _, event in blackboard.received
            )
        )

        await agent.publish(
            AgentCompletedEvent(
                task_id=first.task_id,
                step=1,
                response=AgentResponse(
                    message=Message("assistant", [TextPart("done")]),
                    finish_reason="stop",
                    steps=1,
                ),
            )
        )
        await wait_until(
            lambda: len(
                [
                    event
                    for _, event in blackboard.received
                    if isinstance(event, UserInputEvent)
                ]
            )
            == 2
        )
        await agent.publish(
            AgentErrorEvent(
                task_id=second.task_id,
                step=1,
                error_type="RuntimeError",
                error_message="failed",
            )
        )
        await user_input.drain()
        await manager.stop(timeout=1)
        return first, second, blackboard.received, backend.received

    first, second, blackboard_events, backend_events = asyncio.run(run())

    user_events = [
        event
        for _, event in blackboard_events
        if isinstance(event, UserInputEvent)
    ]
    assert [event.prompt for event in user_events] == ["first", "second"]
    finished = [
        event
        for _, event in backend_events
        if isinstance(event, InputFinishedEvent)
    ]
    assert [(event.task_id, event.status) for event in finished] == [
        (first.task_id, "completed"),
        (second.task_id, "failed"),
    ]


def test_user_input_plugin_未启动时拒绝submit():
    async def run():
        plugin = UserInputPlugin("user-input", SessionStub())
        with pytest.raises(RuntimeError, match="not running"):
            await plugin.submit("hello")

    asyncio.run(run())
