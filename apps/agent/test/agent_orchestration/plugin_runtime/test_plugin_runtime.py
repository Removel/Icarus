import asyncio

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
