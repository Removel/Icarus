import asyncio
from contextlib import contextmanager

import pytest

from apps.agent.src.agent_orchestration.capability import (
    AgentCancelledEvent,
    AgentCompletedEvent,
    AgentResponse,
)
from apps.agent.src.agent_orchestration.run_control import TaskChannelRegistry
from apps.agent.src.agent_orchestration.events import Event, TaskErrorEvent
from apps.agent.src.agent_orchestration.plugin_runtime import BasePlugin, PluginManager
from apps.agent.src.agent_orchestration.plugins.persistence import ImageAssetError
from apps.agent.src.agent_orchestration.plugins import (
    InputFinishedEvent,
    InputQueuedEvent,
    InputStartedEvent,
    UserInputEvent,
    UserInputPlugin,
)
from apps.agent.src.model_provider.types import ImagePart, Message, TextPart


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
            TaskErrorEvent(
                task_id=second.task_id,
                fatal=True,
                code="agent_run_failed",
                step=1,
                run_id="run-2",
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


def test_user_input_plugin准备阶段取消直接收口并开始下一task():
    async def run():
        manager = PluginManager()
        channels = TaskChannelRegistry()
        user_input = UserInputPlugin(
            "user-input",
            SessionStub(),
            task_channels=channels,
        )
        sink = SinkPlugin("sink")
        agent = SinkPlugin("agent")
        for plugin in (user_input, sink, agent):
            manager.register(plugin)
        manager.subscribe("sink", "user-input")
        manager.subscribe("user-input", "agent")
        await manager.start()

        first = await user_input.submit("first")
        second = await user_input.submit("second")
        await wait_until(
            lambda: any(
                isinstance(event, UserInputEvent) and event.task_id == first.task_id
                for _, event in sink.received
            )
        )
        channels.request_cancel(first.task_id, "user_requested")
        await wait_until(
            lambda: any(
                isinstance(event, UserInputEvent) and event.task_id == second.task_id
                for _, event in sink.received
            )
        )
        await agent.publish(
            AgentCompletedEvent(
                task_id=second.task_id,
                step=1,
                response=AgentResponse(
                    message=Message("assistant", [TextPart("done")])
                ),
            )
        )
        await user_input.drain()
        await manager.stop(timeout=1)
        return first, second, sink.received, channels

    first, second, events, channels = asyncio.run(run())
    finished = [
        event for _, event in events if isinstance(event, InputFinishedEvent)
    ]

    assert [(event.task_id, event.status) for event in finished] == [
        (first.task_id, "cancelled"),
        (second.task_id, "completed"),
    ]
    assert channels.request_cancel(first.task_id).status == "already_finished"


def test_user_input_plugin运行阶段等待agent取消终态():
    async def run():
        manager = PluginManager()
        channels = TaskChannelRegistry()
        user_input = UserInputPlugin(
            "user-input",
            SessionStub(),
            task_channels=channels,
        )
        sink = SinkPlugin("sink")
        agent = SinkPlugin("agent")
        for plugin in (user_input, sink, agent):
            manager.register(plugin)
        manager.subscribe("sink", "user-input")
        manager.subscribe("user-input", "agent")
        await manager.start()

        accepted = await user_input.submit("first")
        await wait_until(
            lambda: any(
                isinstance(event, UserInputEvent)
                for _, event in sink.received
            )
        )
        channel = channels.get(accepted.task_id)
        assert channel is not None
        channel.start_run("run-1")
        channels.request_cancel(accepted.task_id, "user_requested")
        await asyncio.sleep(0)
        assert not any(
            isinstance(event, InputFinishedEvent)
            for _, event in sink.received
        )

        channel.mark_cancelled()
        await agent.publish(
            AgentCancelledEvent(
                task_id=accepted.task_id,
                step=0,
                reason="user_requested",
            )
        )
        await user_input.drain()
        await manager.stop(timeout=1)
        return accepted, sink.received

    accepted, events = asyncio.run(run())
    finished = [
        event for _, event in events if isinstance(event, InputFinishedEvent)
    ]
    assert [(event.task_id, event.status) for event in finished] == [
        (accepted.task_id, "cancelled")
    ]


def test_user_input_plugin导入图片后只发布稳定引用(tmp_path):
    class ImageSession(SessionStub):
        def import_image(self, path):
            assert path == tmp_path / "input.png"
            return ImagePart("assets/hash.png", "asset", "image/png")

    async def run():
        manager = PluginManager()
        channels = TaskChannelRegistry()
        user_input = UserInputPlugin(
            "user-input", ImageSession(), task_channels=channels
        )
        sink = SinkPlugin("sink")
        agent = SinkPlugin("agent")
        for plugin in (user_input, sink, agent):
            manager.register(plugin)
        manager.subscribe("sink", "user-input")
        manager.subscribe("user-input", "agent")
        await manager.start()
        accepted = await user_input.submit(
            "describe", [tmp_path / "input.png"]
        )
        await wait_until(
            lambda: any(
                isinstance(event, UserInputEvent)
                for _, event in sink.received
            )
        )
        await agent.publish(
            AgentCompletedEvent(
                task_id=accepted.task_id,
                step=1,
                response=AgentResponse(
                    Message("assistant", [TextPart("done")])
                ),
            )
        )
        await user_input.drain()
        await manager.stop(timeout=1)
        return sink.received

    events = asyncio.run(run())
    user_event = next(
        event for _, event in events if isinstance(event, UserInputEvent)
    )
    assert user_event.input_images == [
        ImagePart("assets/hash.png", "asset", "image/png")
    ]
    assert str(tmp_path) not in repr(user_event)


def test_user_input_plugin图片导入失败直接结束且不发布用户输入(tmp_path):
    class FailingImageSession(SessionStub):
        def import_image(self, path):
            del path
            raise ImageAssetError("image file is unavailable")

    async def run():
        manager = PluginManager()
        channels = TaskChannelRegistry()
        user_input = UserInputPlugin(
            "user-input", FailingImageSession(), task_channels=channels
        )
        sink = SinkPlugin("sink")
        for plugin in (user_input, sink):
            manager.register(plugin)
        manager.subscribe("sink", "user-input")
        await manager.start()
        accepted = await user_input.submit(
            "describe", [tmp_path / "missing.png"]
        )
        await user_input.drain()
        await manager.stop(timeout=1)
        return accepted, sink.received

    accepted, events = asyncio.run(run())
    task_events = [
        event for _, event in events if event.task_id == accepted.task_id
    ]
    assert [type(event) for event in task_events] == [
        InputQueuedEvent,
        InputStartedEvent,
        TaskErrorEvent,
        InputFinishedEvent,
    ]
    assert task_events[2].code == "image_import_failed"
    assert task_events[3].status == "failed"
    assert not any(isinstance(event, UserInputEvent) for event in task_events)


def test_user_input_plugin错误事件只接受agent和blackboard来源():
    plugin = UserInputPlugin("user-input", SessionStub())
    error = TaskErrorEvent(
        task_id="task-1",
        fatal=True,
        code="failed",
        error_type="RuntimeError",
        error_message="failed",
    )

    assert plugin.accepts_event("agent", error) is True
    assert plugin.accepts_event("blackboard", error) is True
    assert plugin.accepts_event("user-input", error) is False
