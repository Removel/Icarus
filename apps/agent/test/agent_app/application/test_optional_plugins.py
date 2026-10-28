"""Removing an explicit required override allows tier-based startup degradation."""

import asyncio

from agent_app.application.session_runtime import SessionRuntime
from apps.agent.test.agent_app.application.test_session_runtime import make_config
from icarus.core.identity import SessionIdentity


def test_disabled_memory_does_not_leave_required_region(tmp_path, monkeypatch):
    async def fail_start(self):
        raise RuntimeError("memory self-check failed")
    monkeypatch.setattr("agent_app.plugins.memory.plugin.MemoryPlugin.on_session_start", fail_start)

    async def run():
        config = make_config(tmp_path / "data")
        config.runtime.required_plugin_ids.remove("memory")
        runtime = SessionRuntime(SessionIdentity.create(tmp_path, "optional"), config=config,
                                 publish_update=lambda update: asyncio.sleep(0))
        await runtime.start()
        try:
            assert "memory" not in runtime.runtime_host.graph.registrations
            board = runtime.runtime_host.get_plugin("blackboard")
            assert "memory" not in {definition.region for definition in board.region_registry.definitions()}
            assert runtime.is_running
        finally:
            await runtime.stop("test")
    asyncio.run(run())
