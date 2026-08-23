import asyncio

import pytest

from apps.agent.src.agent_orchestration.plugin_runtime import PluginManager, PluginStatus

from apps.agent.test.agent_orchestration.plugin_runtime.support import (
    RecordingPlugin,
    SampleEvent,
)


def test_plugin_manager_统一启动停止并支持注销():
    async def run():
        manager = PluginManager()
        producer = RecordingPlugin("producer")
        consumer = RecordingPlugin("consumer")
        manager.register(producer)
        manager.register(consumer)
        subscription = manager.subscribe("consumer", "producer")
        await manager.start()
        await producer.publish(SampleEvent(value="hello"))
        await manager.stop(timeout=1)
        snapshot = manager.get_runtime_snapshot("consumer")
        manager.unsubscribe(subscription.subscription_id)
        removed = manager.unregister("consumer")
        return producer, consumer, snapshot, removed

    producer, consumer, snapshot, removed = asyncio.run(run())

    assert producer.started is True
    assert producer.stopped is True
    assert consumer.events[0].value == "hello"
    assert snapshot.status == PluginStatus.STOPPED
    assert removed is consumer


def test_plugin_manager_运行后拒绝注册():
    async def run():
        manager = PluginManager()
        manager.register(RecordingPlugin("first"))
        await manager.start()
        try:
            with pytest.raises(RuntimeError, match="before manager start"):
                manager.register(RecordingPlugin("second"))
        finally:
            await manager.stop(timeout=1)

    asyncio.run(run())


def test_plugin_manager_运行中冻结订阅图():
    async def run():
        manager = PluginManager()
        manager.register(RecordingPlugin("producer"))
        manager.register(RecordingPlugin("consumer"))
        subscription = manager.subscribe("consumer", "producer")
        await manager.start()
        try:
            with pytest.raises(RuntimeError, match="before manager start"):
                manager.subscribe("producer", "consumer")
            with pytest.raises(RuntimeError, match="while manager is running"):
                manager.unsubscribe(subscription.subscription_id)
        finally:
            await manager.stop(timeout=1)

        manager.unsubscribe(subscription.subscription_id)

    asyncio.run(run())


def test_plugin_manager_启动失败时回滚已启动runtime():
    class FailingStartPlugin(RecordingPlugin):
        async def start(self) -> None:
            raise RuntimeError("start failed")

    async def run():
        manager = PluginManager()
        first = RecordingPlugin("first")
        failing = FailingStartPlugin("failing")
        manager.register(first)
        manager.register(failing)

        with pytest.raises(RuntimeError, match="start failed"):
            await manager.start()

        return first, manager.get_runtime_snapshot("first")

    first, snapshot = asyncio.run(run())

    assert first.stopped is True
    assert snapshot.status == PluginStatus.STOPPED
