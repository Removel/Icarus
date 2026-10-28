"""Lifecycle coordinator for one manifest-driven Plugin Runtime."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
import logging
from pathlib import Path
from types import MappingProxyType

from typing import cast
from icarus.core.run_control import TaskChannelRegistry
from icarus.runtime.recording import RuntimeRecorder
from icarus.core.recording import bind_record_sink, reset_record_sink
from icarus.core.hooks import HookDispatcher
from icarus.runtime.diagnostics import (
    RuntimeDiagnostics,
)
from icarus.runtime.assembly import (
    PluginAssembly,
)
from icarus.runtime.manager import (
    PluginManager,
)
from icarus.runtime.resolver import (
    RequiredPluginError,
)
from icarus.runtime.state_coordinator import (
    PluginStateCoordinator,
)
from icarus.core.types import (
    RuntimeGraphSnapshot,
    RuntimeHostStatus,
)
from icarus.core.events.catalog import EventCatalog
from icarus.core.capabilities.catalog import CapabilityCatalog
from icarus.core.tools import ToolRegistry


class PluginRuntimeHost:
    """Coordinate graph creation, Plugin lifecycle and persisted state."""

    def __init__(
        self,
        workspace_path: str | Path,
        session_id: str,
        *,
        plugin_dirs: tuple[str | Path, ...] = (),
        builtin_package: str | None = None,
        required_plugin_ids: frozenset[str] = frozenset(),
        plugin_configs: Mapping[str, Mapping[str, object]] | None = None,
        plugin_manager: PluginManager | None = None,
        tool_registry: ToolRegistry | None = None,
        event_catalog: EventCatalog | None = None,
        capability_catalog: CapabilityCatalog | None = None,
        logger: logging.Logger | None = None,
        before_restore: Callable[["PluginRuntimeHost"], Awaitable[None]] | None = None,
        before_ready: Callable[["PluginRuntimeHost"], Awaitable[None]] | None = None,
    ) -> None:
        self.before_restore = before_restore
        self.before_ready = before_ready
        self.recorder = RuntimeRecorder()
        self.task_channels: TaskChannelRegistry | None = None
        self.workspace_path = Path(workspace_path).expanduser().resolve()
        self.session_id = session_id
        self.plugin_manager = plugin_manager or PluginManager()
        self.tool_registry = tool_registry or ToolRegistry()
        self.logger = logger or logging.getLogger(__name__)
        self.required_plugin_ids = frozenset(required_plugin_ids)
        self.diagnostics = RuntimeDiagnostics()
        configs = {
            key: MappingProxyType(dict(value))
            for key, value in (plugin_configs or {}).items()
        }
        self.graph = PluginAssembly(
            self.workspace_path,
            session_id,
            plugin_dirs=tuple(plugin_dirs),
            builtin_package=builtin_package,
            required_plugin_ids=self.required_plugin_ids,
            plugin_configs=configs,
            plugin_manager=self.plugin_manager,
            tool_registry=self.tool_registry,
            event_catalog=event_catalog,
            capability_catalog=capability_catalog,
            diagnostics=self.diagnostics,
            logger=self.logger,
        )
        self.state: PluginStateCoordinator | None = None
        self.graph_snapshot: RuntimeGraphSnapshot | None = None
        self._status: RuntimeHostStatus = "created"

    @property
    def is_running(self) -> bool:
        return self.status in {"ready", "running"}

    @property
    def status(self) -> RuntimeHostStatus:
        if self._status == "ready":
            task_channels = getattr(self, "task_channels", None)
            if task_channels is not None and task_channels.active_task_ids:
                return "running"
        return self._status

    @status.setter
    def status(self, value: RuntimeHostStatus) -> None:
        self._status = value

    def get_plugin(self, plugin_id: str):
        return self.graph.get_plugin(plugin_id)

    def get_capability(self, capability_id: str) -> object:
        contract = self.graph.capability_catalog.resolve(capability_id)
        if contract.access_mode == "event":
            raise TypeError("Event capabilities must use a requester")
        return self.graph.capability_registry.get_host_port(capability_id)

    async def start(self) -> None:
        token = bind_record_sink(self.recorder)
        try:
            await self._start_impl()
        finally:
            reset_record_sink(token)

    async def _start_impl(self) -> None:
        if self.status != "created":
            raise RuntimeError(f"Runtime Host cannot start from {self.status}")
        started: list[str] = []
        try:
            disabled = await self.graph.build(self._set_status)
            self.required_plugin_ids = self.graph.required_plugin_ids
            for registration in self.graph.registrations.values():
                for backend in registration.backends:
                    self.recorder.register_backend(backend)
            self.recorder.start()
            dispatcher = self.plugin_manager.hook_dispatcher
            if dispatcher is not None:
                dispatcher.recorder = self.recorder
                dispatcher.registry.recorder = self.recorder
            if self.before_restore is not None:
                await self.before_restore(self)
            self.state = PluginStateCoordinator(
                self.graph.discovered,
                self.graph.registrations,
                self.graph.capability_registry,
            )
            self.status = "starting"
            for plugin_id in tuple(self.graph.registrations):
                if self.graph.has_disabled_provider(plugin_id, disabled):
                    await self.graph.disable_started_plugin(
                        plugin_id,
                        disabled,
                        "disabled_dependency",
                        "Capability provider failed during startup",
                    )
                    continue
                try:
                    await self.plugin_manager.start_plugin(plugin_id)
                    started.append(plugin_id)
                    self.state.refresh_state_store()
                    self.status = "restoring"
                    try:
                        await self.state.restore_plugin_state(plugin_id)
                        await self.graph.registrations[plugin_id].plugin.on_session_start()
                    except Exception as error:
                        if plugin_id in self.required_plugin_ids:
                            raise
                        await self.graph.disable_started_plugin(
                            plugin_id,
                            disabled,
                            "plugin_state_restore_failed",
                            str(error),
                            level="warning",
                        )
                    self.status = "starting"
                except Exception as error:
                    if plugin_id in self.required_plugin_ids:
                        raise RequiredPluginError(
                            f"Required Plugin failed to start: "
                            f"{plugin_id}: {error}"
                        ) from error
                    await self.graph.disable_started_plugin(
                        plugin_id,
                        disabled,
                        "plugin_start_failed",
                        str(error),
                    )
            await self.graph.cascade_runtime_unavailable(disabled)
            self.state.refresh_state_store()
            self.graph_snapshot = self.graph.build_snapshot(
                self.workspace_path, self.session_id, disabled
            )
            if self.before_ready is not None:
                await self.before_ready(self)
            self.tool_registry.freeze()
            await self.plugin_manager.start()
            self.status = "ready"
        except BaseException:
            await self.graph.rollback(started)
            self.recorder.stop()
            self.status = "failed"
            raise

    async def checkpoint(
        self, plugin_ids: tuple[str, ...]
    ) -> tuple[str, ...]:
        if self.status not in {"ready", "running"}:
            raise RuntimeError(
                f"Runtime Host cannot checkpoint from {self.status}"
            )
        await self.plugin_manager.event_bus.drain()
        for plugin_id in plugin_ids:
            await self.plugin_manager.drain_plugin(plugin_id)
        if self.state is None:
            return ()
        return tuple(await self.state.snapshot_plugin_states(plugin_ids))

    async def stop(self, timeout: float | None = 30) -> tuple[str, ...]:
        token = bind_record_sink(self.recorder)
        try:
            return await self._stop_impl(timeout)
        finally:
            reset_record_sink(token)

    async def _stop_impl(self, timeout: float | None = 30) -> tuple[str, ...]:
        if self.status == "stopped":
            return ()
        if not self.plugin_manager.is_running:
            self.status = "stopped"
            self.graph.remove_import_roots()
            return ()
        errors: list[str] = []
        self.status = "quiescing"
        try:
            await self.plugin_manager.quiesce()
        except Exception as error:
            errors.append(f"quiesce: {error}")
        try:
            operation = self.plugin_manager.drain()
            if timeout is None:
                await operation
            else:
                await asyncio.wait_for(operation, timeout=timeout)
        except Exception as error:
            errors.append(f"drain: {error}")
        self.status = "snapshotting"
        if self.state is not None:
            errors.extend(
                await self.state.snapshot_states(self.graph_snapshot)
            )
        self.status = "stopping"
        try:
            await self.plugin_manager.stop(timeout=timeout, drain=False)
        except Exception as error:
            errors.append(f"stop: {error}")
        self.recorder.stop()
        self.status = "stopped"
        self.graph.remove_import_roots()
        return tuple(errors)

    def _set_status(self, status: str) -> None:
        self.status = cast(RuntimeHostStatus, status)
