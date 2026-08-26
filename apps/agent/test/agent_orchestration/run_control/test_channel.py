import asyncio

import pytest

from apps.agent.src.agent_orchestration.run_control import (
    AgentRunCancelled,
    MaxStepsExceededError,
    TaskChannel,
    TaskChannelRegistry,
    TaskChannelStatus,
)
from apps.agent.src.model_provider.types import Message, TextPart, Usage


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
        assert channel.cancel_reason == "user_requested"
        with pytest.raises(AgentRunCancelled):
            channel.raise_if_cancelled()

    asyncio.run(run())


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
