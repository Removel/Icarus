"""Explicit handler dispatch and a single plugin tool export point."""

import asyncio
from dataclasses import dataclass

from icarus.core.events import Event
from icarus.core import plugin as api


@dataclass(frozen=True, kw_only=True)
class Input(Event):
    value: str


@dataclass(frozen=True, kw_only=True)
class Output(Event):
    value: str


def test_declared_handler_returns_events_through_bound_publisher():
    assert hasattr(api, "handles"), "declarative plugin handler API is missing"

    class Plugin(api.BasePlugin):
        @api.handles(Input)
        async def on_input(self, source, event):
            return (Output(value=source + event.value),)

    async def run():
        plugin = Plugin("example")
        output = []
        async def publish(event):
            output.append(event)
        plugin.bind_publisher(publish)
        await plugin.dispatch("origin", Input(value="-value"))
        assert len(output) == 1
        assert output[0].value == "origin-value"
        assert plugin.build_tools() == ()
    asyncio.run(run())


def test_tool_only_plugin_does_not_need_empty_consume():
    plugin = api.BasePlugin("tools")
    assert plugin.build_tools() == ()
