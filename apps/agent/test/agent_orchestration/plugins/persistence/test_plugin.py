import asyncio
import logging

from apps.agent.src.agent_orchestration.hooks import HookRegistry
from apps.agent.src.agent_orchestration.plugins.persistence import (
    PersistencePlugin,
    PersistenceRuntime,
    SessionIdentity,
)


def test_persistence_plugin分别保存workspace和session状态(tmp_path):
    data_dir = tmp_path / "data"
    workspace = tmp_path / "workspace"
    runtime = PersistenceRuntime(data_dir, workspace)
    identity = SessionIdentity.create(workspace, "session-1")
    plugin = PersistencePlugin(
        "persistence",
        runtime,
        identity,
        logging.getLogger(__name__),
        HookRegistry(),
    )
    asyncio.run(plugin.start())

    plugin.save_plugin_state(
        "sample", "1.0.0", "hash", "workspace", 1, {"value": "w"}
    )
    plugin.save_plugin_state(
        "sample", "1.0.0", "hash", "session", 2, {"value": "s"}
    )

    assert plugin.load_plugin_state("sample", "workspace")["state"] == {
        "value": "w"
    }
    assert plugin.load_plugin_state("sample", "session")["state"] == {
        "value": "s"
    }
    assert plugin._state_path("sample", "workspace") != plugin._state_path(
        "sample", "session"
    )
    asyncio.run(plugin.stop())
