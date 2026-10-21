"""Starting an already-subscribed runtime retains the existing subscriber."""

import asyncio
from agent_app.application import AgentRuntime
from apps.agent.test.agent_app.application.test_agent_runtime import make_config


def test_subscription_before_start_is_preserved(tmp_path):
    async def run():
        runtime = AgentRuntime(config_loader=lambda: make_config(tmp_path / "data"))
        subscriber = runtime.subscribe_updates()
        try:
            await runtime.start()
            await runtime.create_session(tmp_path, "lazy", load_runtime=False)
            assert not subscriber.closed
        finally:
            subscriber.close()
            await runtime.stop()
    asyncio.run(run())
