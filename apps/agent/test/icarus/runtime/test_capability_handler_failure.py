"""Incomplete capability handlers fail deterministically instead of timing out."""

import asyncio
from icarus.core.capabilities import ConversationReadRequest, CapabilityStatus
from icarus.core.plugin import BasePlugin, handles
from icarus.runtime.manager import PluginManager


def test_capability_handler_without_response_returns_failed():
    async def run():
        class Provider(BasePlugin):
            @handles(ConversationReadRequest)
            async def read(self, source, event):
                return None
        manager = PluginManager()
        caller, provider = BasePlugin("caller"), Provider("provider")
        manager.register(caller); manager.register(provider)
        manager.bind_capability("conversation.read", "provider", required_by={"caller"})
        await manager.start()
        try:
            result = await caller.request(ConversationReadRequest(timeout_seconds=0.01))
            assert result.status is CapabilityStatus.FAILED
        finally:
            await manager.stop()
    asyncio.run(run())
