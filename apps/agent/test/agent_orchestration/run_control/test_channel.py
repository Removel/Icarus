import asyncio

import pytest

from apps.agent.src.agent_orchestration.run_control import (
    AgentRunCancelled,
    MaxStepsExceededError,
    TaskChannel,
    TaskChannelRegistry,
    TaskChannelStatus,
)
from apps.agent.src.model_provider.types import ImagePart, Message, TextPart, Usage


def test_task_channel按fifo合并补充信息():
    channel = TaskChannel("task-1")
    assert channel.mark_preparing_context() is True
    assert channel.start_run("run-1") is True

    assert channel.add_context("first", source_id="memory").status == "accepted"
    assert channel.add_context("second", source_id="external").status == "accepted"

    batch = channel.drain_context(applied_before_step=2)

    assert batch is not None
    assert [record.content for record in batch.records] == ["first", "second"]
    assert batch.message.content[0].text == (
        "<runtime_context>\n1. first\n2. second\n</runtime_context>"
    )
    assert batch.applied_before_step == 2
    assert channel.applied_batches == (batch,)


def test_task_channel在同一批次区分context和用户steer():
    channel = TaskChannel("task-1")
    channel.mark_preparing_context()
    channel.start_run("run-1")

    assert channel.add_steer("only tests").status == "accepted"
    assert channel.add_context("remember preference", source_id="memory").status == (
        "accepted"
    )
    assert channel.add_steer("do not commit").status == "accepted"

    batch = channel.drain_context(applied_before_step=2)

    assert batch is not None
    assert [record.kind for record in batch.records] == [
        "user_correction",
        "context",
        "user_correction",
    ]
    assert batch.message.content[0].text == (
        "<runtime_context>\n1. remember preference\n</runtime_context>\n\n"
        "<user_correction>\n1. only tests\n2. do not commit\n"
        "</user_correction>"
    )


def test_task_channel将steer图片放入同一条用户消息():
    channel = TaskChannel("task-1")
    channel.mark_preparing_context()
    channel.start_run("run-1")
    image = ImagePart("assets/image.png", "asset", "image/png")

    result = channel.add_steer(
        "look here", input_images=(image,), display_text="look [#image1]"
    )
    batch = channel.drain_context(applied_before_step=2)

    assert result.status == "accepted"
    assert batch is not None
    assert batch.message.content == [
        TextPart("<user_correction>\n1. look here\n</user_correction>"),
        image,
    ]
    assert batch.records[0].display_text == "look [#image1]"


def test_task_channel接受阶段接收context并拒绝空内容或来源():
    channel = TaskChannel("task-1")

    assert channel.add_context("early", source_id="external").status == (
        "accepted"
    )
    assert channel.add_context("   ", source_id="external").status == (
        "invalid_content"
    )
    assert channel.add_context("content", source_id="   ").status == (
        "invalid_content"
    )


def test_task_channel保存最近协议完整消息快照():
    channel = TaskChannel("task-1")
    channel.mark_preparing_context()
    channel.start_run("run-1")
    messages = [Message("user", [TextPart("first")])]

    channel.checkpoint_history(messages)
    messages.append(Message("assistant", [TextPart("partial")]))

    assert channel.history_checkpoint == (
        Message("user", [TextPart("first")]),
    )

    channel.checkpoint_history(messages)

    assert channel.history_checkpoint == tuple(messages)

    channel.request_cancel("stop")
    channel.checkpoint_history(
        [*messages, Message("assistant", [TextPart("late")])]
    )

    assert channel.history_checkpoint == tuple(messages)


def test_task_channel最终关闭与补充信息原子竞争():
    channel = TaskChannel("task-1")
    channel.mark_preparing_context()
    channel.start_run("run-1")

    assert channel.close_or_drain(applied_before_step=2) is None
    assert channel.status == TaskChannelStatus.RUNNING
    assert channel.add_context("late", source_id="memory").status == (
        "already_finished"
    )
    assert channel.request_cancel().status == "already_finished"
    assert channel.mark_completed() is True
    assert channel.status == TaskChannelStatus.COMPLETED


def test_task_channel取消优先并拒绝后续context():
    async def run():
        channel = TaskChannel("task-1")
        channel.mark_preparing_context()
        waiter = asyncio.create_task(channel.wait_cancel_requested())

        first = channel.request_cancel("user_requested")
        await waiter
        second = channel.request_cancel("again")
        context = channel.add_context("late", source_id="external")

        assert first.status == "accepted"
        assert second.status == "already_cancelling"
        assert context.status == "already_cancelling"
        assert channel.add_steer("late steer").status == "already_cancelling"
        assert channel.cancel_reason == "user_requested"
        with pytest.raises(AgentRunCancelled):
            channel.raise_if_cancelled()

    asyncio.run(run())


def test_task_channel停止丢弃尚未应用的steer但保留已应用记录():
    channel = TaskChannel("task-1")
    channel.mark_preparing_context()
    channel.start_run("run-1")
    channel.add_steer("applied")
    applied = channel.drain_context(applied_before_step=2)
    channel.add_steer("discarded")

    assert channel.request_cancel("stop").status == "accepted"

    assert applied is not None
    assert [record.content for record in channel.applied_batches[0].records] == [
        "applied"
    ]
    assert [record.content for record in channel.discarded_records] == [
        "discarded"
    ]
    with pytest.raises(AgentRunCancelled):
        channel.drain_context(applied_before_step=3)


def test_task_channel_registry管理唯一通道():
    registry = TaskChannelRegistry()
    channel = registry.create("task-1")

    assert registry.get("task-1") is channel
    with pytest.raises(ValueError, match="already exists"):
        registry.create("task-1")
    assert registry.request_cancel("missing").status == "not_found"
    assert registry.finish("task-1") is channel
    assert registry.get("task-1") is None
    assert registry.request_cancel("task-1").status == "already_finished"


def test_task_channel_registry仅有界保留已结束task():
    registry = TaskChannelRegistry(finished_task_limit=2)

    for index in range(3):
        task_id = f"task-{index}"
        channel = registry.create(task_id)
        channel.mark_preparing_context()
        channel.start_run(f"run-{index}")
        registry.finish(task_id)

    assert registry.request_cancel("task-0").status == "not_found"
    result = registry.request_cancel("task-1")
    assert result.status == "already_finished"
    assert result.run_id == "run-1"
    assert registry.create("task-0").task_id == "task-0"


def test_task_channel_registry向活动task追加steer并稳定拒绝已结束task():
    registry = TaskChannelRegistry()
    channel = registry.create("task-1")
    channel.mark_preparing_context()
    channel.start_run("run-1")

    accepted = registry.add_steer("task-1", "change direction")
    registry.finish("task-1")
    finished = registry.add_steer("task-1", "too late")

    assert accepted.status == "accepted"
    assert finished.status == "already_finished"
    assert finished.run_id == "run-1"


def test_task_channel终态不能互相覆盖():
    completed = TaskChannel("completed")
    completed.mark_preparing_context()
    completed.start_run("run-completed")
    assert completed.mark_completed() is True
    assert completed.mark_failed() is False
    assert completed.mark_cancelled() is False
    assert completed.status == TaskChannelStatus.COMPLETED

    cancelling = TaskChannel("cancelling")
    cancelling.mark_preparing_context()
    assert cancelling.request_cancel().status == "accepted"
    assert cancelling.mark_failed() is False
    assert cancelling.mark_completed() is False
    assert cancelling.mark_cancelled() is True
    assert cancelling.status == TaskChannelStatus.CANCELLED


def test_task_channel保存usage并在第257步前截停():
    channel = TaskChannel("task-1", max_steps=256)
    channel.mark_preparing_context()
    channel.start_run("run-1")
    usage = Usage(10, 2)

    channel.checkpoint_history(
        [Message("user", [TextPart("hello")])], usage
    )
    channel.raise_if_step_exceeded(256)

    assert channel.history_checkpoint_usage == usage
    with pytest.raises(MaxStepsExceededError) as caught:
        channel.raise_if_step_exceeded(257)
    assert caught.value.attempted_step == 257


def test_task_channel达到step上限时不把待处理steer标记为已应用():
    channel = TaskChannel("task-1", max_steps=1)
    channel.mark_preparing_context()
    channel.start_run("run-1")
    channel.add_steer("too late")

    with pytest.raises(MaxStepsExceededError):
        channel.close_or_drain(applied_before_step=2)

    assert channel.applied_batches == ()
