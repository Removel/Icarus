import asyncio

import pytest

from icarus.core.agents import (
    AgentTextDeltaEvent,
    AgentToolCompletedEvent,
    AgentToolStartedEvent,
)
from icarus.core.tools import ToolExecutionResult
from icarus.core.models.messages import ToolCall
from icarus.core.hooks import (
    BaseHook,
    HookDispatcher,
    HookEvent,
    HookRegistry,
)
from icarus.runtime import PluginManager

from apps.agent.test.icarus.runtime.support import (
    RecordingPlugin,
    SampleEvent,
)


from apps.agent.test.icarus.recording_support import CaptureRecords as RecordingHook


def test_runtime_hook_观测发布路由消费和生命周期():
    async def run():
        hook_registry = HookRegistry()
        hook = RecordingHook()
        hook_registry.recorder = hook
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


@pytest.mark.parametrize(
    "event",
    [
        AgentTextDeltaEvent(step=1, text="hello"),
        AgentToolStartedEvent(
            step=1,
            tool_call=ToolCall(id="call-1", name="read", arguments={}),
        ),
        AgentToolCompletedEvent(
            step=1,
            tool_call=ToolCall(id="call-1", name="read", arguments={}),
            result=ToolExecutionResult(success=True, output="done"),
        ),
    ],
)
def test_runtime_hook_增量事件正常路由但不记录事件流hook(event):
    async def run():
        hook_registry = HookRegistry()
        hook = RecordingHook()
        hook_registry.recorder = hook
        manager = PluginManager(
            hook_dispatcher=HookDispatcher(hook_registry),
        )
        producer = RecordingPlugin("producer")
        consumer = RecordingPlugin("consumer")
        manager.register(producer)
        manager.register(consumer)
        manager.subscribe("consumer", "producer")
        await manager.start()
        await producer.publish(event)
        await manager.stop(timeout=1)
        return event, consumer.events, hook.events

    event, consumed, hook_events = asyncio.run(run())

    assert consumed == [event]
    assert not [
        hook_event
        for hook_event in hook_events
        if hook_event.name in {"event.publish", "event.route", "plugin.consume"}
    ]
    assert [
        hook_event
        for hook_event in hook_events
        if hook_event.name == "plugin.lifecycle"
    ]
