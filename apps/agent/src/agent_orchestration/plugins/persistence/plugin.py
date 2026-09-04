"""Persistence Plugin adapter and state store capability."""

from collections.abc import Mapping
from pathlib import Path

from apps.agent.src.agent_orchestration.events import Event
from apps.agent.src.agent_orchestration.plugin_runtime import BasePlugin
from apps.agent.src.agent_orchestration.plugins.persistence.runtime import (
    PersistenceRuntime,
)
from apps.agent.src.agent_orchestration.plugins.persistence.session_identity import (
    SessionIdentity,
)


class PersistencePlugin(BasePlugin):
    def __init__(
        self,
        plugin_id: str,
        runtime: PersistenceRuntime,
        identity: SessionIdentity,
        logger,
        hook_registry,
    ) -> None:
        super().__init__(plugin_id)
        self.runtime = runtime
        self.identity = identity
        self.logger = logger
        self.hook_registry = hook_registry

    async def start(self) -> None:
        self.runtime.start(
            self.hook_registry,
            self.logger,
            session_identity=self.identity,
        )

    async def consume(self, source_plugin_id: str, event: Event) -> None:
        del source_plugin_id, event

    async def stop(self) -> None:
        if self.runtime.is_running:
            self.runtime.stop(drain=True, logger=self.logger)

    def load_plugin_state(
        self, plugin_id: str, scope: str
    ) -> Mapping[str, object] | None:
        path = self._state_path(plugin_id, scope)
        return self.runtime.state_store.read(path)

    def save_plugin_state(
        self,
        plugin_id: str,
        plugin_version: str,
        manifest_hash: str,
        scope: str,
        state_version: int,
        state: Mapping[str, object],
    ) -> None:
        self.runtime.state_store.write(
            self._state_path(plugin_id, scope),
            {
                "plugin_id": plugin_id,
                "plugin_version": plugin_version,
                "manifest_hash": manifest_hash,
                "state_version": state_version,
                "state": dict(state),
            },
        )

    def save_runtime_snapshot(self, snapshot: Mapping[str, object]) -> None:
        self.runtime.state_store.write(
            self.runtime.resolver.session_dir(self.identity)
            / "runtime-snapshot.json",
            dict(snapshot),
        )

    def _state_path(self, plugin_id: str, scope: str) -> Path:
        if scope == "workspace":
            root = self.runtime.resolver.workspace_dir(self.identity)
        elif scope == "session":
            root = self.runtime.resolver.session_dir(self.identity)
        else:
            raise ValueError(f"Unsupported Plugin state scope: {scope}")
        directory = root / "plugin-state"
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        return directory / f"{plugin_id}.json"
