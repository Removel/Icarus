"""Replaceable plugin state backend with independently managed recording outputs."""


from agent_app.plugins.persistence.config import PluginConfig

from collections.abc import Mapping
from pathlib import Path

from icarus.core.capabilities.catalog import StateStoreAccess
from icarus.core.identity import SessionIdentity
from icarus.core.plugin import BasePlugin
from icarus.runtime.paths import DataPathResolver


class PersistencePlugin(BasePlugin):
    config_schema = PluginConfig
    provided_capabilities = (StateStoreAccess,)

    def __init__(self, plugin_id: str, resolver: DataPathResolver, identity: SessionIdentity,
                 logger, state_store, log_handler) -> None:
        super().__init__(plugin_id)
        self.resolver = resolver
        self.identity = identity
        self.logger = logger
        self.state_store = state_store
        self.log_handler = log_handler
        self._logging = False

    async def start(self) -> None:
        if self._logging:
            return
        self.log_handler.bind_session(self.identity)
        self.logger.addHandler(self.log_handler)
        self._logging = True

    async def stop(self) -> None:
        if self._logging:
            self.logger.removeHandler(self.log_handler)
            self.log_handler.close()
            self._logging = False

    def load_plugin_state(self, plugin_id: str, scope: str) -> Mapping[str, object] | None:
        return self.state_store.read(self._state_path(plugin_id, scope))

    def save_plugin_state(self, plugin_id: str, plugin_version: str, manifest_hash: str,
                          scope: str, state_version: int, state: Mapping[str, object]) -> None:
        self.state_store.write(self._state_path(plugin_id, scope), {
            "plugin_id": plugin_id, "plugin_version": plugin_version,
            "manifest_hash": manifest_hash, "state_version": state_version, "state": dict(state),
        })

    def save_runtime_snapshot(self, snapshot: Mapping[str, object]) -> None:
        self.state_store.write(self.resolver.session_dir(self.identity) / "runtime-snapshot.json", dict(snapshot))

    def _state_path(self, plugin_id: str, scope: str) -> Path:
        if scope == "workspace":
            root = self.resolver.workspace_dir(self.identity)
        elif scope == "session":
            root = self.resolver.session_dir(self.identity)
        else:
            raise ValueError(f"Unsupported Plugin state scope: {scope}")
        directory = root / "plugin-state"
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        return directory / f"{plugin_id}.json"
