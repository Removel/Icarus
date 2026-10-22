"""A scoped plugin uses a host-owned transport; it must never close shared services."""

import asyncio

from icarus.core.session import SessionContext


def test_plugin_stop_does_not_close_injected_shared_client():
    from agent_app.plugins.argus.plugin import ArgusPlugin
    async def run():
        closed = []
        class Client:
            async def aclose(self): closed.append(True)
        plugin = ArgusPlugin('argus', context=SessionContext(kind='argus_background'), client=Client())
        await plugin.stop()
        assert closed == []
    asyncio.run(run())
