"""A consume hook rewrites data, not routing or correlation identities."""

import asyncio
from dataclasses import dataclass, replace

from icarus.core.events import Event
from icarus.core.hooks import BaseHook, HookDispatcher, HookRegistry
from icarus.core.plugin import BasePlugin, handles
from icarus.runtime.manager import PluginManager


@dataclass(frozen=True, kw_only=True)
class ValueEvent(Event):
    text: str


def test_runtime_value_rewrite_preserves_event_identity():
    async def run():
        seen = []
        class Consumer(BasePlugin):
            @handles(ValueEvent)
            async def value(self, source, event):
                seen.append((source, event))
        class Rewrite(BaseHook):
            def handle(self, metadata, value):
                return replace(value, text="rewritten")
        hooks = HookRegistry()
        hooks.register("plugin.consume", Rewrite(), priority=1)
        manager = PluginManager(hook_dispatcher=HookDispatcher(hooks))
        source, target = BasePlugin("source"), Consumer("target")
        manager.register(source); manager.register(target)
        manager.subscribe("target", "source")
        await manager.start()
        event = ValueEvent(task_id="task", text="original")
        await source.publish(event)
        await manager.drain()
        await manager.stop()
        assert seen[0][0] == "source"
        assert seen[0][1].event_id == event.event_id
        assert seen[0][1].task_id == "task"
        assert seen[0][1].text == "rewritten"
    asyncio.run(run())
