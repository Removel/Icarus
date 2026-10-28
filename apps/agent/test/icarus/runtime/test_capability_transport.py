"""Directed capability replies must bypass a waiting plugin's serial inbox."""

import asyncio
from dataclasses import dataclass

from icarus.core.capabilities import ConversationReadRequest, ConversationReadResponse, CapabilityStatus
from icarus.core.events import Event
from icarus.core.plugin import BasePlugin, handles
from icarus.runtime.manager import PluginManager


@dataclass(frozen=True, kw_only=True)
class Trigger(Event):
    pass


def test_handler_can_await_response_with_real_serial_workers():
    async def run():
        done = asyncio.Event()
        received = []
        class Caller(BasePlugin):
            @handles(Trigger)
            async def on_trigger(self, source, event):
                response = await self.request(ConversationReadRequest(task_id="task"))
                received.append(response)
                done.set()
        class Provider(BasePlugin):
            @handles(ConversationReadRequest)
            async def read(self, source, event):
                return (ConversationReadResponse(
                    task_id=event.task_id, correlation_id=event.correlation_id,
                    status=CapabilityStatus.OK,
                ),)
        class Source(BasePlugin):
            pass
        manager = PluginManager()
        assert hasattr(manager, "bind_capability"), "capability registry transport is missing"
        caller, provider, source = Caller("caller"), Provider("provider"), Source("source")
        for plugin in (caller, provider, source):
            manager.register(plugin)
        manager.bind_capability("conversation.read", "provider", required_by={"caller"})
        manager.subscribe("caller", "source")
        await manager.start()
        try:
            await source.publish(Trigger())
            await asyncio.wait_for(done.wait(), 1)
            assert received[0].status is CapabilityStatus.OK
            assert caller._requester.pending_count == 0
        finally:
            await manager.stop(timeout=1)
    asyncio.run(run())
