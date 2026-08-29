"""Coordinate Plugin state restore and snapshot persistence."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict

from apps.agent.src.agent_orchestration.plugin_runtime.discovery import (
    DiscoveredPlugin,
)
from apps.agent.src.agent_orchestration.plugin_runtime.registration import (
    PluginRegistration,
    PluginStateStore,
)
from apps.agent.src.agent_orchestration.plugin_runtime.types import (
    RuntimeGraphSnapshot,
)


class PluginStateCoordinator:
    """Keep state persistence separate from Runtime graph construction."""

    def __init__(
        self,
        discovered: dict[str, DiscoveredPlugin],
        registrations: dict[str, PluginRegistration],
        capabilities: dict[tuple[str, str], object],
    ) -> None:
        self.discovered = discovered
        self.registrations = registrations
        self.capabilities = capabilities
        self.state_store: PluginStateStore | None = None

    def refresh_state_store(self) -> None:
        value = self.capabilities.get(("persistence", "state_store"))
        self.state_store = (
            value if isinstance(value, PluginStateStore) else None
        )

    async def restore_plugin_state(self, plugin_id: str) -> None:
        if self.state_store is None:
            return
        registration = self.registrations[plugin_id]
        provider = registration.state_provider
        if provider is None:
            return
        item = self.discovered[plugin_id]
        manifest = item.manifest
        for scope in manifest.state_scopes:
            loaded = self.state_store.load_plugin_state(plugin_id, scope)
            if loaded is None:
                continue
            state = loaded.get("state")
            version = loaded.get("state_version")
            expected = getattr(manifest, f"{scope}_state_version")
            if (
                version != expected
                or not isinstance(state, Mapping)
            ):
                raise RuntimeError(
                    f"Invalid {scope} state for Plugin {plugin_id}"
                )
            method = getattr(provider, f"restore_{scope}_state")
            await method(state, state_version=version)

    async def snapshot_states(
        self,
        graph_snapshot: RuntimeGraphSnapshot | None,
    ) -> list[str]:
        errors = await self.snapshot_plugin_states(
            tuple(self.registrations)
        )
        if self.state_store is None:
            return errors
        if graph_snapshot is not None:
            try:
                self.state_store.save_runtime_snapshot(
                    asdict(graph_snapshot)
                )
            except Exception as error:
                errors.append(f"runtime snapshot: {error}")
        return errors

    async def snapshot_plugin_states(
        self, plugin_ids: tuple[str, ...]
    ) -> list[str]:
        errors: list[str] = []
        if self.state_store is None:
            return errors
        for plugin_id in plugin_ids:
            registration = self.registrations.get(plugin_id)
            if registration is None or registration.state_provider is None:
                continue
            item = self.discovered[plugin_id]
            for scope in item.manifest.state_scopes:
                try:
                    state = await getattr(
                        registration.state_provider,
                        f"snapshot_{scope}_state",
                    )()
                    if state is not None:
                        self.state_store.save_plugin_state(
                            plugin_id,
                            item.manifest.plugin_version,
                            item.manifest_hash,
                            scope,
                            getattr(item.manifest, f"{scope}_state_version"),
                            state,
                        )
                except Exception as error:
                    errors.append(f"snapshot {plugin_id}/{scope}: {error}")
        return errors
