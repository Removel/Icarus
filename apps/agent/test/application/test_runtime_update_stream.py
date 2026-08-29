import asyncio
from datetime import UTC, datetime

import pytest

from apps.agent.src.application.runtime_update_stream import (
    RuntimeUpdateOverflowError,
    RuntimeUpdateStream,
)
from apps.agent.src.runtime_update import RuntimeUpdate


def update(text):
    return RuntimeUpdate(
        workspace_key="workspace",
        session_id="session",
        task_id="task",
        type="assistant.text_delta",
        payload={"text": text, "step": 1},
        occurred_at=datetime.now(UTC),
    )


def test_runtime_update_stream独立广播并保持顺序():
    async def run():
        stream = RuntimeUpdateStream(capacity=2)
        first = stream.subscribe()
        second = stream.subscribe()
        await stream.publish(update("a"))
        await stream.publish(update("b"))
        result = (
            [await first.next_update(), await first.next_update()],
            [await second.next_update(), await second.next_update()],
        )
        stream.close()
        return result

    first, second = asyncio.run(run())
    assert [item.payload["text"] for item in first] == ["a", "b"]
    assert second == first


def test_runtime_update_stream溢出只关闭慢订阅():
    async def run():
        stream = RuntimeUpdateStream(capacity=1)
        slow = stream.subscribe()
        fast = stream.subscribe()
        await stream.publish(update("a"))
        await fast.next_update()
        await stream.publish(update("b"))
        fast_item = await fast.next_update()
        with pytest.raises(RuntimeUpdateOverflowError):
            await slow.next_update()
        stream.close()
        return fast_item, slow.closed

    fast_item, slow_closed = asyncio.run(run())
    assert fast_item.payload["text"] == "b"
    assert slow_closed is True


def test_runtime_update_stream关闭唤醒等待者():
    async def run():
        stream = RuntimeUpdateStream()
        subscription = stream.subscribe()
        waiter = asyncio.create_task(subscription.next_update())
        await asyncio.sleep(0)
        stream.close()
        with pytest.raises(RuntimeError, match="closed"):
            await waiter

    asyncio.run(run())
