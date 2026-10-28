"""Dispatchers may reject malformed handlers without allowing provider impersonation."""

import asyncio
from icarus.core.capabilities import ConversationReadRequest, ConversationReadResponse, CapabilityStatus
from icarus.core.plugin import BasePlugin, handles
from icarus.runtime.manager import PluginManager


def test_capability_requests_cannot_be_broadcast_as_business_events():
    async def run():
        manager = PluginManager()
        plugin = BasePlugin("source")
        manager.register(plugin)
        await manager.start()
        try:
            try:
                await plugin.publish(ConversationReadRequest())
            except RuntimeError as error:
                assert "capability" in str(error).lower()
            else:
                raise AssertionError("capability request escaped onto ordinary subscriptions")
        finally:
            await manager.stop()
    asyncio.run(run())
