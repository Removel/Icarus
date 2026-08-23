"""Objects returned by Plugin factories for atomic Runtime registration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Protocol, runtime_checkable

from apps.agent.src.agent_orchestration.plugin_runtime.base_plugin import BasePlugin
from apps.agent.src.agent_orchestration.tools import BaseTool


@dataclass(frozen=True)
class ProvidedCapability:
    capability_id: str
    version: str
    value: object


@runtime_checkable
class PluginStateProvider(Protocol):
    async def restore_workspace_state(
        self,
        state: Mapping[str, object],
        *,
        state_version: int,
    ) -> None: ...

    async def restore_session_state(
        self,
        state: Mapping[str, object],
        *,
        state_version: int,
    ) -> None: ...

    async def snapshot_workspace_state(self) -> Mapping[str, object] | None: ...

    async def snapshot_session_state(self) -> Mapping[str, object] | None: ...


@runtime_checkable
class PluginStateStore(Protocol):
    def load_plugin_state(
        self,
        plugin_id: str,
        scope: str,
    ) -> Mapping[str, object] | None: ...

    def save_plugin_state(
        self,
        plugin_id: str,
        plugin_version: str,
        manifest_hash: str,
        scope: str,
        state_version: int,
        state: Mapping[str, object],
    ) -> None: ...

    def save_runtime_snapshot(self, snapshot: Mapping[str, object]) -> None: ...


@dataclass(frozen=True)
class PluginRegistration:
    plugin: BasePlugin
    capabilities: tuple[ProvidedCapability, ...] = ()
    tools: tuple[BaseTool, ...] = ()
    state_provider: PluginStateProvider | None = None
