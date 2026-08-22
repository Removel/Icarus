import asyncio

import pytest

from apps.agent.src.agent_orchestration.events import Event
from apps.agent.src.application.output_bridge import OutputBridgePlugin


def test_output_bridge_向每个实时订阅按来源与顺序广播事件():
    async def run():
        bridge = OutputBridgePlugin()
        first_subscription = bridge.subscribe()
        second_subscription = bridge.subscribe()
        first = Event(task_id="task-1")
        second = Event(task_id="task-2")

        await bridge.consume("user-input", first)
        await bridge.consume("agent", second)
        first_items = [
            await first_subscription.next_event(),
            await first_subscription.next_event(),
        ]
        second_items = [
            await second_subscription.next_event(),
            await second_subscription.next_event(),
        ]
        first_subscription.close()
        second_subscription.close()
        return first, second, first_items, second_items

    first, second, first_items, second_items = asyncio.run(run())

    expected = [("user-input", first), ("agent", second)]
    assert first_items == expected
    assert second_items == expected


def test_output_bridge_只发送订阅后事件并暂存未及时消费事件():
    async def run():
        bridge = OutputBridgePlugin()
        before_subscription = Event(task_id="before-subscription")
        first = Event(task_id="task-1")
        second = Event(task_id="task-2")
        await bridge.consume("agent", before_subscription)

        subscription = bridge.subscribe()
        await bridge.consume("agent", first)
        await bridge.consume("agent", second)
        await asyncio.sleep(0)

        items = [
            await subscription.next_event(),
            await subscription.next_event(),
        ]
        subscription.close()
        return items

    items = asyncio.run(run())

    assert [event.task_id for _, event in items] == ["task-1", "task-2"]


def test_output_bridge_关闭单个订阅不影响其他订阅():
    async def run():
        bridge = OutputBridgePlugin()
        closed_subscription = bridge.subscribe()
        active_subscription = bridge.subscribe()
        closed_subscription.close()

        event = Event(task_id="task-1")
        await bridge.consume("agent", event)
        active_item = await active_subscription.next_event()
        with pytest.raises(RuntimeError, match="subscription is closed"):
            await closed_subscription.next_event()
        active_subscription.close()
        return event, active_item

    event, active_item = asyncio.run(run())

    assert active_item == ("agent", event)


def test_output_bridge_stop关闭全部订阅并唤醒等待者():
    async def run():
        bridge = OutputBridgePlugin()
        subscription = bridge.subscribe()
        waiter = asyncio.create_task(subscription.next_event())
        await asyncio.sleep(0)

        await bridge.stop()

        with pytest.raises(RuntimeError, match="subscription is closed"):
            await asyncio.wait_for(waiter, timeout=1)
        return subscription.closed

    assert asyncio.run(run()) is True


def test_output_bridge_关闭订阅会结束同一订阅的并发等待者():
    async def run():
        bridge = OutputBridgePlugin()
        subscription = bridge.subscribe()
        waiters = [
            asyncio.create_task(subscription.next_event()),
            asyncio.create_task(subscription.next_event()),
        ]
        await asyncio.sleep(0)

        subscription.close()

        results = await asyncio.wait_for(
            asyncio.gather(*waiters, return_exceptions=True),
            timeout=1,
        )
        return results

    results = asyncio.run(run())

    assert len(results) == 2
    assert all(
        isinstance(result, RuntimeError)
        and str(result) == "Output event subscription is closed"
        for result in results
    )
