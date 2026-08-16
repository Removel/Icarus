import asyncio

from apps.agent.src.agent_orchestration.hooks import (
    BaseHook,
    HookDispatcher,
    HookEvent,
    HookRegistry,
)
from apps.agent.src.agent_orchestration.plugin_runtime import PluginManager

from apps.agent.test.agent_orchestration.plugin_runtime.support import (
    RecordingPlugin,
    SampleEvent,
)


class RecordingHook(BaseHook):
    def __init__(self) -> None:
        self.events: list[HookEvent] = []

    def handle(self, event: HookEvent) -> None:
        self.events.append(event)


def test_runtime_hook_观测发布路由消费和生命周期():
    async def run():
        hook_registry = HookRegistry()
        hook = RecordingHook()
        hook_registry.register("*", hook)
        manager = PluginManager(
            hook_dispatcher=HookDispatcher(hook_registry),
        )
        producer = RecordingPlugin("producer")
        consumer = RecordingPlugin("consumer")
        manager.register(producer)
        manager.register(consumer)
        manager.subscribe("consumer", "producer")
        await manager.start()
        await producer.publish(SampleEvent(value="hello"))
        await manager.stop(timeout=1)
        return hook.events

    events = asyncio.run(run())
    names = [event.name for event in events]

    assert "event.publish" in names
    assert "event.route" in names
    assert "plugin.consume" in names
    assert "plugin.lifecycle" in names
