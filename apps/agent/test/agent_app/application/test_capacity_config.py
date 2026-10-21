"""Runtime capacities follow explicit overrides, not duplicated hardcoded defaults."""

import asyncio
from agent_app.application import AgentRuntime
from apps.agent.test.agent_app.application.test_session_runtime import make_config


def test_runtime_capacity_uses_config_and_honors_explicit_override(tmp_path):
    async def run():
        config = make_config(tmp_path / "data")
        config.runtime.update_queue_capacity = 7
        runtime = AgentRuntime(config_loader=lambda: config)
        await runtime.start()
        assert runtime._updates.capacity == 7
        await runtime.stop()
        override = AgentRuntime(config_loader=lambda: config, update_queue_capacity=3)
        await override.start()
        assert override._updates.capacity == 3
        await override.stop()
    asyncio.run(run())
