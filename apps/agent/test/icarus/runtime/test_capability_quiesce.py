import asyncio
import pytest

from icarus.core.hooks import HookDispatcher, HookRegistry
from icarus.runtime.manager import PluginManager
from icarus.core.plugin import BasePlugin


def test_quiesce_rejects_new_capability_requests_before_worker_drain():
    async def run():
        manager = PluginManager()
        caller = BasePlugin("caller")
        manager.register(caller)
        await manager.start()
        await manager.quiesce()
        from icarus.core.capabilities import ConversationReadRequest, CapabilityStatus
        response = await caller.request(ConversationReadRequest())
        assert response.status is CapabilityStatus.UNAVAILABLE
        await manager.stop()
    asyncio.run(run())
