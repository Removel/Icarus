import asyncio
from types import SimpleNamespace

import pytest

from apps.agent.src.agent_orchestration.plugin_runtime import (
    BasePlugin,
    PluginRegistration,
    PluginStateCoordinator,
)


class StatefulPlugin(BasePlugin):
    def __init__(self) -> None:
        super().__init__("stateful")
        self.restored = None

    async def consume(self, source_plugin_id, event):
        del source_plugin_id, event

    async def restore_session_state(self, state, *, state_version):
        self.restored = (dict(state), state_version)

    async def restore_workspace_state(self, state, *, state_version):
        del state, state_version

    async def snapshot_session_state(self):
        return None

    async def snapshot_workspace_state(self):
        return None


class StateStore:
    def __init__(self, value):
        self.value = value

    def load_plugin_state(self, plugin_id, scope):
        del plugin_id, scope
        return self.value

    def save_plugin_state(self, *args, **kwargs):
        del args, kwargs

    def save_runtime_snapshot(self, snapshot):
        del snapshot


def coordinator(value):
    plugin = StatefulPlugin()
    manifest = SimpleNamespace(
        state_scopes=("session",),
        session_state_version=1,
        plugin_version="2.0.0",
    )
    result = PluginStateCoordinator(
        {
            "stateful": SimpleNamespace(
                manifest=manifest, manifest_hash="current-hash"
            )
        },
        {
            "stateful": PluginRegistration(
                plugin=plugin, state_provider=plugin
            )
        },
        {("persistence", "state_store"): StateStore(value)},
    )
    result.refresh_state_store()
    return result, plugin


def test_state_coordinator_state_version相同忽略plugin版本和manifest_hash变化():
    state, plugin = coordinator(
        {
            "plugin_version": "1.0.0",
            "manifest_hash": "old-hash",
            "state_version": 1,
            "state": {"value": "restored"},
        }
    )

    asyncio.run(state.restore_plugin_state("stateful"))

    assert plugin.restored == ({"value": "restored"}, 1)


def test_state_coordinator_state_version不同拒绝恢复():
    state, plugin = coordinator(
        {
            "plugin_version": "2.0.0",
            "manifest_hash": "current-hash",
            "state_version": 2,
            "state": {"value": "blocked"},
        }
    )

    with pytest.raises(RuntimeError, match="Invalid session state"):
        asyncio.run(state.restore_plugin_state("stateful"))

    assert plugin.restored is None
