"""Session infrastructure is shared and owned by the host, not AgentPlugin."""

import asyncio
import importlib

from apps.agent.test.agent_app.application.test_session_runtime import make_config
from icarus.core.identity import SessionIdentity


def test_task_channels_are_session_owned(tmp_path):
    async def run():
        module = importlib.import_module("icarus.runtime.session")
        from agent_app.application.session_runtime import SessionRuntime
        runtime = SessionRuntime(
            SessionIdentity.create(tmp_path, "channels"),
            config=make_config(tmp_path / "data"),
            publish_update=lambda update: asyncio.sleep(0),
        )
        assert isinstance(runtime, module.SessionHost)
        await runtime.start()
        try:
            agent = runtime.runtime_host.get_plugin("agent")
            inputs = runtime.runtime_host.get_plugin("user-input")
            assert runtime.task_channels is agent.task_channels is inputs.task_channels
        finally:
            await runtime.stop("test")
    asyncio.run(run())
