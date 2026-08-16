import asyncio

from apps.agent.src.agent_orchestration.events import Event
from apps.agent.src.agent_orchestration.plugin_runtime import BasePlugin, PluginManager

from apps.agent.test.agent_orchestration.plugin_runtime.support import (
    RecordingPlugin,
    SampleEvent,
)


class RelayPlugin(BasePlugin):
    def __init__(self, plugin_id: str) -> None:
        super().__init__(plugin_id)
        self.sources = []
        self.values = []

    async def consume(self, source_plugin_id: str, event: Event) -> None:
        if isinstance(event, SampleEvent):
            self.sources.append(source_plugin_id)
            self.values.append(event.value)
            await self.publish(SampleEvent(value=f"relay:{event.value}"))


def test_runtime_多来源统一消费并再次发布():
    async def run():
        manager = PluginManager()
        first = RecordingPlugin("first")
        second = RecordingPlugin("second")
        relay = RelayPlugin("relay")
        sink = RecordingPlugin("sink")
        for plugin in (first, second, relay, sink):
            manager.register(plugin)
        manager.subscribe("relay", "first")
        manager.subscribe("relay", "second")
        manager.subscribe("sink", "relay")
        await manager.start()
        await first.publish(SampleEvent(value="a"))
        await second.publish(SampleEvent(value="b"))
        await manager.stop(timeout=1)
        return relay.sources, relay.values, [event.value for event in sink.events]

    relay_sources, relay_values, sink_values = asyncio.run(run())

    assert relay_sources == ["first", "second"]
    assert relay_values == ["a", "b"]
    assert sink_values == ["relay:a", "relay:b"]
