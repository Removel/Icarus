import asyncio

from apps.agent.src.agent_orchestration.events import Event
from apps.agent.src.application.output_bridge import OutputBridgePlugin


def test_output_bridge_按来源与入队顺序原样转发事件():
    async def run():
        bridge = OutputBridgePlugin()
        first = Event(correlation_id="task-1")
        second = Event(correlation_id="task-2")

        await bridge.consume("user-input", first)
        await bridge.consume("agent", second)
        first_item = await bridge.next_event()
        bridge.task_done()
        second_item = await bridge.next_event()
        bridge.task_done()
        await asyncio.wait_for(bridge._events.join(), timeout=1)
        return first, second, first_item, second_item

    first, second, first_item, second_item = asyncio.run(run())

    assert first_item == ("user-input", first)
    assert second_item == ("agent", second)


def test_output_bridge_discard_pending清空未消费事件并完成队列计数():
    async def run():
        bridge = OutputBridgePlugin()
        await bridge.consume("agent", Event(correlation_id="task-1"))
        await bridge.consume("agent", Event(correlation_id="task-2"))

        discarded = bridge.discard_pending()
        await asyncio.wait_for(bridge._events.join(), timeout=1)
        return discarded, bridge._events.empty()

    discarded, is_empty = asyncio.run(run())

    assert discarded == 2
    assert is_empty is True
