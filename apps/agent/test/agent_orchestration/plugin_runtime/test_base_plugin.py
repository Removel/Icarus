import asyncio

import pytest

from apps.agent.test.agent_orchestration.plugin_runtime.support import (
    RecordingPlugin,
    SampleEvent,
)


def test_base_plugin_未绑定bus时发布失败():
    plugin = RecordingPlugin("producer")

    with pytest.raises(RuntimeError, match="not bound"):
        asyncio.run(plugin.publish(SampleEvent(value="hello")))


def test_base_plugin_只能通过绑定入口以自身身份发布():
    plugin = RecordingPlugin("producer")
    events = []

    async def publish(event):
        events.append((plugin.plugin_id, event))

    plugin.bind_publisher(publish)
    asyncio.run(plugin.publish(SampleEvent(value="hello")))

    assert events[0][0] == "producer"
    assert events[0][1].value == "hello"
