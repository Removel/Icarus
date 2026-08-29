import asyncio
from datetime import UTC

import pytest

from apps.agent.src.agent_orchestration.plugin_runtime import (
    PluginRegistry,
    PluginRuntime,
    PluginStatus,
    PublishedEvent,
)

from apps.agent.test.agent_orchestration.plugin_runtime.support import (
    RecordingPlugin,
    SampleEvent,
)


def test_plugin_runtime_统一队列按顺序消费多个来源():
    async def run():
        registry = PluginRegistry()
        plugin = RecordingPlugin("consumer")
        registry.register(plugin)
        runtime = PluginRuntime(plugin, registry)
        await runtime.start()
        await runtime.enqueue(PublishedEvent("source-a", SampleEvent(value="a")))
        await runtime.enqueue(PublishedEvent("source-b", SampleEvent(value="b")))
        await runtime.drain()
        snapshot = runtime.snapshot()
        await runtime.stop()
        return plugin, snapshot, runtime

    plugin, snapshot, runtime = asyncio.run(run())

    assert [event.value for event in plugin.events] == ["a", "b"]
    assert snapshot.processed_count == 2
    assert snapshot.failed_count == 0
    assert runtime.status == PluginStatus.STOPPED


def test_plugin_runtime_消费失败后继续处理下一条():
    async def run():
        registry = PluginRegistry()
        plugin = RecordingPlugin("consumer", fail_values={"bad"})
        registry.register(plugin)
        runtime = PluginRuntime(plugin, registry)
        await runtime.start()
        await runtime.enqueue(PublishedEvent("source", SampleEvent(value="bad")))
        await runtime.enqueue(PublishedEvent("source", SampleEvent(value="good")))
        await runtime.drain()
        snapshot = runtime.snapshot()
        await runtime.stop()
        return plugin, snapshot

    plugin, snapshot = asyncio.run(run())

    assert [event.value for event in plugin.events] == ["good"]
    assert snapshot.processed_count == 1
    assert snapshot.failed_count == 1
    assert snapshot.last_error is None


def test_plugin_runtime_登记并清理后台工作():
    async def run():
        registry = PluginRegistry()
        plugin = RecordingPlugin("consumer")
        registry.register(plugin)
        runtime = PluginRuntime(plugin, registry)
        plugin.bind_background_work_starter(runtime.start_background_work)
        gate = asyncio.Event()

        async def work():
            await gate.wait()

        await runtime.start()
        task = plugin.start_background_work(work, name="index")
        await asyncio.sleep(0)
        active = runtime.snapshot()
        gate.set()
        await task
        finished = runtime.snapshot()
        await runtime.stop()
        return active, finished

    active, finished = asyncio.run(run())

    assert active.background_work_count == 1
    assert len(active.active_background_works) == 1
    assert active.active_background_works[0].name == "index"
    assert active.active_background_works[0].started_at.tzinfo == UTC
    assert finished.background_work_count == 0
    assert finished.last_background_work_at is not None
    assert finished.background_failed_count == 0


def test_plugin_runtime_后台失败进入独立诊断且不禁用plugin():
    async def run():
        registry = PluginRegistry()
        plugin = RecordingPlugin("consumer")
        registry.register(plugin)
        runtime = PluginRuntime(plugin, registry)
        plugin.bind_background_work_starter(runtime.start_background_work)

        async def fail():
            raise RuntimeError("safe failure")

        await runtime.start()
        await plugin.start_background_work(fail, name="failing")
        snapshot = runtime.snapshot()
        await runtime.stop()
        return snapshot

    snapshot = asyncio.run(run())

    assert snapshot.status == PluginStatus.RUNNING
    assert snapshot.background_work_count == 0
    assert snapshot.background_failed_count == 1
    assert snapshot.last_background_error == "safe failure"
    assert snapshot.last_error is None


def test_plugin_runtime_quiesce后拒绝新后台工作且不调用factory():
    async def run():
        registry = PluginRegistry()
        plugin = RecordingPlugin("consumer")
        registry.register(plugin)
        runtime = PluginRuntime(plugin, registry)
        plugin.bind_background_work_starter(runtime.start_background_work)
        called = False

        async def work():
            nonlocal called
            called = True

        await runtime.start()
        await runtime.quiesce()
        with pytest.raises(RuntimeError, match="not accepting background work"):
            plugin.start_background_work(work, name="late")
        await runtime.stop()
        return called

    assert asyncio.run(run()) is False


def test_plugin_runtime_drain等待已登记后台工作():
    async def run():
        registry = PluginRegistry()
        plugin = RecordingPlugin("consumer")
        registry.register(plugin)
        runtime = PluginRuntime(plugin, registry)
        plugin.bind_background_work_starter(runtime.start_background_work)
        gate = asyncio.Event()

        await runtime.start()
        plugin.start_background_work(gate.wait, name="wait")
        drain = asyncio.create_task(runtime.drain())
        await asyncio.sleep(0)
        waiting = not drain.done()
        gate.set()
        await drain
        await runtime.stop()
        return waiting

    assert asyncio.run(run()) is True
