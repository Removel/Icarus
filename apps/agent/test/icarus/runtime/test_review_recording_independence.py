"""Records remain available when a generic host has no HookDispatcher."""

import asyncio
from icarus.core.recording import record
from icarus.core.plugin import BasePlugin
from icarus.core.registration import PluginRegistration
from icarus.runtime.host import PluginRuntimeHost


def test_generic_host_records_without_hook_dispatcher(tmp_path):
    rows = []
    class Backend:
        def start(self): pass
        def offer(self, row): rows.append(row); return True
        def drain(self): pass
        def stop(self, drain=True): pass
    class Plugin(BasePlugin):
        async def start(self): record("custom.start", "after", {"value": 1})
    async def run():
        host = PluginRuntimeHost(tmp_path, "records")
        async def build(set_status):
            plugin = Plugin("sample")
            host.graph.registrations["sample"] = PluginRegistration(plugin, backends=(Backend(),))
            host.plugin_manager.register(plugin)
            return set()
        host.graph.build = build
        await host.start()
        await host.stop()
    asyncio.run(run())
    assert any(row.name == "custom.start" for row in rows)
    assert any(row.name == "plugin.lifecycle" for row in rows)
