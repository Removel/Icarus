import asyncio
import time

import pytest

from apps.agent.src.agent_orchestration.plugin_runtime import PluginManager

from apps.agent.test.agent_orchestration.plugin_runtime.support import (
    RecordingPlugin,
    SampleEvent,
)


def test_event_bus_publish_只等待接受不等待慢消费者():
    async def run():
        manager = PluginManager()
        producer = RecordingPlugin("producer")
        consumer = RecordingPlugin("consumer", delay=0.08)
        manager.register(producer)
        manager.register(consumer)
        manager.subscribe("consumer", "producer")
        await manager.start()
        started_at = time.monotonic()
        await producer.publish(SampleEvent(value="hello"))
        elapsed = time.monotonic() - started_at
        await manager.stop(timeout=1)
        return elapsed, consumer.events

    elapsed, events = asyncio.run(run())

    assert elapsed < 0.04
    assert events[0].value == "hello"


def test_event_bus_同一事件扇出且不检查事件类型():
    async def run():
        manager = PluginManager()
        producer = RecordingPlugin("producer")
        first = RecordingPlugin("first")
        second = RecordingPlugin("second")
        for plugin in (producer, first, second):
            manager.register(plugin)
        manager.subscribe("first", "producer")
        manager.subscribe("second", "producer")
        await manager.start()
        event = SampleEvent(value="fanout")
        await producer.publish(event)
        await manager.stop(timeout=1)
        return event, first.events, second.events

    event, first_events, second_events = asyncio.run(run())

    assert first_events == [event]
    assert second_events == [event]


def test_event_bus_拒绝非运行来源发布():
    async def run():
        manager = PluginManager()
        producer = RecordingPlugin("producer")
        manager.register(producer)
        await manager.event_bus.start()
        try:
            with pytest.raises(RuntimeError, match="not running"):
                await manager.event_bus.publish(
                    "producer",
                    SampleEvent(value="hello"),
                )
        finally:
            await manager.event_bus.stop(drain=False)

    asyncio.run(run())
