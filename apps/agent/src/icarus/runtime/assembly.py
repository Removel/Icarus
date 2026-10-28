"""Build and maintain one immutable Plugin capability graph."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Literal
import importlib
import inspect
import logging
from pathlib import Path
import sys
from threading import RLock
from types import MappingProxyType

from packaging.version import Version

from icarus.core.manifest import ProvidedCapabilityManifest, RequiredCapabilityManifest
from icarus.core.events import Event
from icarus.core.events.catalog import EventCatalog, builtin_event_catalog
from icarus.core.capabilities.catalog import CapabilityCatalog, builtin_capability_catalog
from icarus.runtime.contracts import ContractLoader
from icarus.runtime.capability_registry import CapabilityRegistry
from icarus.runtime.diagnostics import (
    RuntimeDiagnostics,
)
from icarus.runtime.discovery import (
    DiscoveredPlugin,
    PluginManifestDiscovery,
)
from icarus.runtime.manager import (
    PluginManager,
)
from icarus.core.registration import (
    PluginRegistration,
    PluginStateProvider,
)
from icarus.runtime.resolver import (
    RequiredPluginError,
    resolve_plugins,
)
from icarus.core.types import (
    RuntimeGraphSnapshot,
    RuntimePluginSnapshot,
)
from icarus.core.tools import ToolChecker, ToolRegistry


_IMPORT_ROOT_LOCK = RLock()
_IMPORT_ROOT_REFERENCES: dict[str, int] = {}


class PluginAssembly:
    """Own discovery, Factory validation and atomic Runtime registration."""

    def __init__(
        self,
        workspace_path: Path,
        session_id: str,
        *,
        plugin_dirs: tuple[str | Path, ...],
        builtin_package: str | None,
        required_plugin_ids: frozenset[str],
        plugin_configs: Mapping[str, Mapping[str, object]],
        plugin_manager: PluginManager,
        tool_registry: ToolRegistry,
        diagnostics: RuntimeDiagnostics,
        logger: logging.Logger,
        event_catalog: EventCatalog | None = None,
        capability_catalog: CapabilityCatalog | None = None,
    ) -> None:
        self.workspace_path = workspace_path
        self.session_id = session_id
        self.plugin_dirs = plugin_dirs
        self.builtin_package = builtin_package
        self.required_plugin_ids = required_plugin_ids
        self.plugin_configs = plugin_configs
        self.plugin_manager = plugin_manager
        self.tool_registry = tool_registry
        self.diagnostics = diagnostics
        self.logger = logger
        self.discovered: dict[str, DiscoveredPlugin] = {}
        self.registrations: dict[str, PluginRegistration] = {}
        self.capabilities: dict[tuple[str, str], object] = {}
        self._owned_import_roots: list[str] = []
        self._contract_import_roots: set[str] = set()
        self.event_catalog = event_catalog.copy() if event_catalog is not None else builtin_event_catalog()
        self.capability_catalog = capability_catalog.copy() if capability_catalog is not None else builtin_capability_catalog()
        self.capability_registry = CapabilityRegistry()
        self.event_chains: tuple[tuple[str, str, str], ...] = ()

    def get_plugin(self, plugin_id: str):
        try:
            return self.registrations[plugin_id].plugin
        except KeyError as error:
            raise KeyError(f"Plugin is not enabled: {plugin_id}") from error

    def get_capability(self, plugin_id: str, capability_id: str) -> object:
        try:
            return self.capabilities[(plugin_id, capability_id)]
        except KeyError as error:
            raise KeyError(
                f"Capability is not enabled: {plugin_id}/{capability_id}"
            ) from error

    async def build(self, set_status: Callable[[str], None]) -> set[str]:
        set_status("discovering")
        discovery = PluginManifestDiscovery(
            self.plugin_dirs, builtin_package=self.builtin_package
        ).discover()
        self.diagnostics.items.extend(discovery.diagnostics.items)
        self.discovered = {item.manifest.plugin_id: item for item in discovery.plugins}
        self.required_plugin_ids = self.required_plugin_ids | frozenset(
            plugin_id for plugin_id, item in self.discovered.items() if item.manifest.tier == "base"
        )
        set_status("resolving")
        prerequisites = resolve_plugins(
            discovery.plugins,
            required_plugin_ids=self.required_plugin_ids,
            diagnostics=self.diagnostics,
        )
        rejected = set(prerequisites.disabled_plugin_ids)
        candidates = prerequisites.plugins
        for item in candidates:
            if item.import_root is not None:
                self._acquire_import_root(str(item.import_root))
        set_status("registering_contracts")
        loader = ContractLoader(self.event_catalog, self.capability_catalog,
                                importlib.import_module, self.diagnostics)
        rejected.update(loader.load(candidates))
        self._retain_contract_import_roots(candidates, loader.definitions)
        rejected.update(self._validate_contract_references(candidates, rejected))
        set_status("resolving")
        graph = resolve_plugins(
            tuple(item for item in candidates if item.manifest.plugin_id not in rejected),
            required_plugin_ids=self.required_plugin_ids,
            diagnostics=self.diagnostics,
        )
        disabled = rejected | set(graph.disabled_plugin_ids)
        enabled = {item.manifest.plugin_id for item in graph.plugins}
        for plugin_id in self.discovered.keys() - enabled:
            self.remove_plugin_import_root(plugin_id)
        self.discovered = {item.manifest.plugin_id: item for item in graph.plugins}
        set_status("validating")
        for item in graph.plugins:
            plugin_id = item.manifest.plugin_id
            if self.has_disabled_provider(plugin_id, disabled):
                self.disable_plugin(
                    plugin_id,
                    disabled,
                    "disabled_dependency",
                    "Capability provider failed during construction",
                )
                continue
            dependencies: dict[tuple[str, str], object] = {}
            registration: PluginRegistration | None = None
            try:
                registration = await self._call_factory(item, dependencies)
                self._validate_registration(item, registration)
                for requirement in item.manifest.required_capabilities:
                    self.capability_catalog.resolve(requirement.capability_id)
            except Exception as error:
                if registration is not None:
                    try:
                        await registration.plugin.stop()
                    except Exception:
                        self.logger.exception(
                            "Invalid Plugin registration cleanup failed: %s",
                            plugin_id,
                        )
                if plugin_id in self.required_plugin_ids:
                    raise RequiredPluginError(
                        f"Required Plugin Factory failed: {plugin_id}: {error}"
                    ) from error
                self.disable_plugin(
                    plugin_id, disabled, "factory_failed", str(error)
                )
                continue
            self.registrations[plugin_id] = registration
            for capability in registration.capabilities:
                self.capabilities[(plugin_id, capability.capability_id)] = (
                    capability.value
                )
                self.capability_registry.bind(capability.capability_id, capability.version, plugin_id, capability.value)

        await self._validate_events_and_cascade(disabled)
        self._register_graph()
        self._record_unmatched_events()
        return disabled

    def _retain_contract_import_roots(
        self, plugins: tuple[DiscoveredPlugin, ...], definitions: set[type],
    ) -> None:
        modules = {item.manifest.contracts for item in plugins if item.manifest.contracts is not None}
        modules.update(definition.__module__ for definition in definitions)
        for module_name in modules:
            module = sys.modules.get(module_name)
            module_file = getattr(module, "__file__", None)
            if module_file is None:
                continue
            for import_root in self._owned_import_roots:
                try:
                    Path(module_file).resolve().relative_to(Path(import_root).resolve())
                except ValueError:
                    continue
                self._contract_import_roots.add(import_root)

    def _validate_contract_references(
        self, plugins: tuple[DiscoveredPlugin, ...], rejected: set[str],
    ) -> set[str]:
        invalid: set[str] = set()
        for item in plugins:
            manifest = item.manifest
            if manifest.plugin_id in rejected:
                continue
            for event_id in (*manifest.published_events, *manifest.consumed_events):
                try:
                    self.event_catalog.resolve(event_id)
                except KeyError:
                    invalid.add(manifest.plugin_id)
                    self.diagnostics.add(manifest.plugin_id, "unknown_event", f"Unknown event ID: {event_id}")
            declarations: tuple[ProvidedCapabilityManifest | RequiredCapabilityManifest, ...] = (
                *manifest.provided_capabilities, *manifest.required_capabilities,
            )
            for declaration in declarations:
                try:
                    contract = self.capability_catalog.resolve(declaration.capability_id)
                except KeyError:
                    invalid.add(manifest.plugin_id)
                    self.diagnostics.add(manifest.plugin_id, "unknown_capability",
                                         f"Unknown capability ID: {declaration.capability_id}")
                    continue
                if isinstance(declaration, ProvidedCapabilityManifest) and Version(declaration.version) != Version(contract.version):
                    invalid.add(manifest.plugin_id)
                    self.diagnostics.add(manifest.plugin_id, "capability_contract_mismatch",
                                         f"Provided version differs from contract: {declaration.capability_id}")
        return invalid

    async def _validate_events_and_cascade(
        self, disabled: set[str]
    ) -> None:
        for plugin_id, item in tuple(self.discovered.items()):
            if plugin_id not in self.registrations:
                continue
            try:
                for path in (
                    *item.manifest.published_events,
                    *item.manifest.consumed_events,
                ):
                    self._resolve_event(path)
            except Exception as error:
                if plugin_id in self.required_plugin_ids:
                    raise RequiredPluginError(
                        "Required Plugin Event declaration is invalid: "
                        f"{plugin_id}: {error}"
                    ) from error
                await self._discard_registration(
                    plugin_id,
                    disabled,
                    "invalid_event_declaration",
                    str(error),
                )

        changed = True
        while changed:
            changed = False
            for plugin_id, item in tuple(self.discovered.items()):
                if plugin_id not in self.registrations:
                    continue
                missing_providers = sorted(
                    {
                        self.provider_id(dependency.capability_id)
                        for dependency in item.manifest.required_capabilities
                    }.intersection(disabled)
                )
                if not missing_providers:
                    continue
                message = (
                    "Capability providers are disabled: "
                    + ", ".join(missing_providers)
                )
                if plugin_id in self.required_plugin_ids:
                    raise RequiredPluginError(message)
                await self._discard_registration(
                    plugin_id,
                    disabled,
                    "disabled_dependency",
                    message,
                )
                changed = True
            published = {
                event
                for plugin_id, item in self.discovered.items()
                if plugin_id in self.registrations
                for event in item.manifest.published_events
            }
            for plugin_id, item in tuple(self.discovered.items()):
                if plugin_id not in self.registrations:
                    continue
                missing = sorted(
                    set(item.manifest.consumed_events) - set(item.manifest.optional_consumed_events) - published
                )
                if not missing:
                    continue
                message = (
                    "Consumed Events have no enabled publisher: "
                    + ", ".join(missing)
                )
                if plugin_id in self.required_plugin_ids:
                    raise RequiredPluginError(message)
                await self._discard_registration(
                    plugin_id,
                    disabled,
                    "event_publisher_unavailable",
                    message,
                )
                changed = True

    async def _discard_registration(
        self,
        plugin_id: str,
        disabled: set[str],
        code: str,
        message: str,
    ) -> None:
        self.diagnostics.add(plugin_id, code, message)
        disabled.add(plugin_id)
        self.capability_registry.remove_provider(plugin_id)
        registration = self.registrations.pop(plugin_id, None)
        if registration is None:
            self.remove_plugin_import_root(plugin_id)
            return
        for capability in registration.capabilities:
            self.capabilities.pop(
                (plugin_id, capability.capability_id), None
            )
        try:
            await registration.plugin.stop()
        except Exception as error:
            self.diagnostics.add(
                plugin_id,
                "disabled_plugin_cleanup_failed",
                str(error),
            )
        self.remove_plugin_import_root(plugin_id)

    async def _call_factory(
        self,
        item: DiscoveredPlugin,
        dependencies: Mapping[tuple[str, str], object],
    ) -> PluginRegistration:
        user_config = self.plugin_configs.get(item.manifest.plugin_id, {})
        missing = [key for key in item.manifest.required_config if key not in user_config]
        if missing:
            raise ValueError("Missing required plugin config: " + ", ".join(missing))
        import_root = (
            str(item.import_root) if item.import_root is not None else None
        )
        if import_root is not None:
            self._acquire_import_root(import_root)
        module_name, factory_name = item.manifest.entrypoint.split(":", 1)
        module = importlib.import_module(module_name)
        if import_root is not None:
            module_file = getattr(module, "__file__", None)
            if not module_file:
                raise ImportError(
                    f"External Plugin module has no file: {module_name}"
                )
            try:
                Path(module_file).resolve().relative_to(
                    Path(import_root).resolve()
                )
            except (OSError, ValueError) as error:
                raise ImportError(
                    "External Plugin entrypoint resolved outside its "
                    f"configured directory: {module_name}"
                ) from error
        factory = getattr(module, factory_name)
        result = factory(
            plugin_id=item.manifest.plugin_id,
            workspace_path=self.workspace_path,
            session_id=self.session_id,
            config=self.plugin_configs.get(
                item.manifest.plugin_id, MappingProxyType({})
            ),
            required_capabilities=MappingProxyType(dict(dependencies)),
            logger=self.logger.getChild(item.manifest.plugin_id),
        )
        if inspect.isawaitable(result):
            result = await result
        if not isinstance(result, PluginRegistration):
            raise TypeError("Plugin Factory must return PluginRegistration")
        return result

    def _validate_registration(
        self,
        item: DiscoveredPlugin,
        registration: PluginRegistration,
    ) -> None:
        manifest = item.manifest
        schema = registration.plugin.config_schema
        config = self.plugin_configs.get(manifest.plugin_id, {})
        user_config = config.get("_user_config", {})
        if schema is not None and "_user_config" in config:
            schema.model_validate(user_config)
        registration.plugin.validate_manifest(
            manifest,
            {self.event_catalog.resolve(event_id) for event_id in manifest.consumed_events},
            self.capability_catalog,
        )
        if registration.plugin.plugin_id != manifest.plugin_id:
            raise ValueError("Factory Plugin ID does not match Manifest")
        actual_capabilities = {
            capability.capability_id: capability
            for capability in registration.capabilities
        }
        expected_capabilities = {
            capability.capability_id: capability
            for capability in manifest.provided_capabilities
        }
        if actual_capabilities.keys() != expected_capabilities.keys():
            raise ValueError("Factory capabilities do not match Manifest")
        if len(actual_capabilities) != len(registration.capabilities):
            raise ValueError("Factory capabilities must not contain duplicates")
        for capability_id, capability in actual_capabilities.items():
            if capability.value is None:
                raise ValueError("Factory capability value cannot be None")
            contract = self.capability_catalog.resolve(capability_id)
            if contract.access_mode == "host" and contract.port_type is not None:
                if not isinstance(capability.value, contract.port_type):
                    raise TypeError(f"Invalid host capability port: {capability_id}")
            if Version(capability.version) != Version(
                expected_capabilities[capability_id].version
            ):
                raise ValueError(
                    "Factory capability version does not match Manifest"
                )
        declared_tools = registration.plugin.build_tools()
        if declared_tools:
            if registration.tools:
                raise ValueError("Plugin tools must use only build_tools")
            object.__setattr__(registration, "tools", tuple(declared_tools))
        actual_tools = tuple(
            tool.definition.name for tool in registration.tools
        )
        if (
            set(actual_tools) != set(manifest.provided_tools)
            or len(actual_tools) != len(set(actual_tools))
        ):
            raise ValueError("Factory tools do not match Manifest")
        checker = ToolChecker()
        invalid = [
            error
            for tool in registration.tools
            for error in checker.check(tool).errors
        ]
        if invalid:
            raise ValueError(
                "Invalid Plugin Tool: " + "; ".join(invalid)
            )
        if (registration.state_provider is not None) != bool(
            manifest.state_scopes
        ):
            raise ValueError("Factory state provider does not match Manifest")
        if registration.state_provider is not None and not isinstance(
            registration.state_provider, PluginStateProvider
        ):
            raise ValueError("Factory state provider has an invalid interface")

    def _register_graph(self) -> None:
        existing_plugins = set(self.plugin_manager.registry.plugin_ids())
        duplicate_plugins = existing_plugins.intersection(
            self.registrations
        )
        if duplicate_plugins:
            raise RuntimeError(
                "Plugin Registry already contains IDs: "
                + ", ".join(sorted(duplicate_plugins))
            )
        duplicate_tools = set(self.tool_registry.names()).intersection(
            tool.definition.name
            for registration in self.registrations.values()
            for tool in registration.tools
        )
        if duplicate_tools:
            raise RuntimeError(
                "Tool Registry already contains names: "
                + ", ".join(sorted(duplicate_tools))
            )
        published_types = {
            plugin_id: {
                self._resolve_event(path)
                for path in item.manifest.published_events
            }
            for plugin_id, item in self.discovered.items()
            if plugin_id in self.registrations
        }
        consumed_types = {
            plugin_id: {
                self._resolve_event(path)
                for path in item.manifest.consumed_events
            }
            for plugin_id, item in self.discovered.items()
            if plugin_id in self.registrations
        }
        for plugin_id, registration in self.registrations.items():
            self.plugin_manager.register(
                registration.plugin,
                published_event_types=published_types[plugin_id],
                consumed_event_types=consumed_types[plugin_id],
            )
        for capability_id in {
            capability.capability_id
            for plugin_id, item in self.discovered.items()
            if plugin_id in self.registrations
            for capability in item.manifest.provided_capabilities
        }:
            contract = self.capability_catalog.resolve(capability_id)
            if contract.access_mode != "event":
                continue
            provider = self.provider_id(capability_id)
            if provider not in self.registrations:
                continue
            consumers = {p for p, item in self.discovered.items() if p in self.registrations
                         and any(r.capability_id == capability_id for r in item.manifest.required_capabilities)}
            self.plugin_manager.bind_capability(capability_id, provider, required_by=consumers)
        for source_id, source in self.discovered.items():
            if source_id not in self.registrations:
                continue
            published = set(source.manifest.published_events)
            for consumer_id, consumer in self.discovered.items():
                if (
                    consumer_id in self.registrations
                    and published.intersection(
                        consumer.manifest.consumed_events
                    )
                ):
                    self.plugin_manager.subscribe(consumer_id, source_id)
        for plugin_id, registration in self.registrations.items():
            for tool in registration.tools:
                if not self.tool_registry.register(tool):
                    raise RuntimeError(
                        "Plugin Tool registration failed: "
                        f"{plugin_id}/{tool.definition.name}"
                    )

    def _record_unmatched_events(self) -> None:
        self.event_chains = tuple(sorted(
            (source_id, target_id, event_id)
            for source_id, source in self.discovered.items() if source_id in self.registrations
            for target_id, target in self.discovered.items() if target_id in self.registrations
            for event_id in set(source.manifest.published_events) & set(target.manifest.consumed_events)
        ))
        published = {
            event
            for plugin_id, item in self.discovered.items()
            if plugin_id in self.registrations
            for event in item.manifest.published_events
        }
        consumed = {
            event
            for plugin_id, item in self.discovered.items()
            if plugin_id in self.registrations
            for event in item.manifest.consumed_events
        }
        for event in sorted(published - consumed):
            self.diagnostics.add(
                "runtime",
                "event_without_consumer",
                f"Published Event has no enabled consumer: {event}",
                level="warning",
            )

    def _resolve_event(self, event_id: str) -> type[Event]:
        return self.event_catalog.resolve(event_id)

    def provider_id(self, capability_id: str) -> str:
        owners = [p for p, item in self.discovered.items()
                  if item.manifest.capability_version(capability_id) is not None]
        if len(owners) != 1:
            raise KeyError(capability_id)
        return owners[0]

    def has_disabled_provider(
        self, plugin_id: str, disabled: set[str]
    ) -> bool:
        item = self.discovered.get(plugin_id)
        return bool(
            item
            and {
                self.provider_id(dependency.capability_id)
                for dependency in item.manifest.required_capabilities
            }.intersection(disabled)
        )

    async def cascade_runtime_unavailable(
        self, disabled: set[str]
    ) -> None:
        changed = True
        while changed:
            changed = False
            for plugin_id, item in tuple(self.discovered.items()):
                if plugin_id not in self.registrations:
                    continue
                missing_providers = sorted(
                    {
                        self.provider_id(dependency.capability_id)
                        for dependency in item.manifest.required_capabilities
                    }.intersection(disabled)
                )
                if not missing_providers:
                    continue
                message = (
                    "Capability providers failed to start: "
                    + ", ".join(missing_providers)
                )
                if plugin_id in self.required_plugin_ids:
                    raise RequiredPluginError(message)
                await self.disable_started_plugin(
                    plugin_id,
                    disabled,
                    "capability_provider_start_failed",
                    message,
                )
                changed = True
            published = {
                event
                for plugin_id, item in self.discovered.items()
                if plugin_id in self.registrations
                for event in item.manifest.published_events
            }
            for plugin_id, item in tuple(self.discovered.items()):
                if plugin_id not in self.registrations:
                    continue
                missing = sorted(
                    set(item.manifest.consumed_events) - set(item.manifest.optional_consumed_events) - published
                )
                if not missing:
                    continue
                message = (
                    "Consumed Events have no started publisher: "
                    + ", ".join(missing)
                )
                if plugin_id in self.required_plugin_ids:
                    raise RequiredPluginError(message)
                await self.disable_started_plugin(
                    plugin_id,
                    disabled,
                    "event_publisher_start_failed",
                    message,
                )
                changed = True

    def disable_plugin(
        self,
        plugin_id: str,
        disabled: set[str],
        code: str,
        message: str,
        *,
        level: Literal["warning", "error"] = "error",
    ) -> None:
        self.diagnostics.add(plugin_id, code, message, level=level)
        if plugin_id in self.required_plugin_ids:
            raise RequiredPluginError(message)
        disabled.add(plugin_id)
        self.capability_registry.remove_provider(plugin_id)
        registration = self.registrations.pop(plugin_id, None)
        if registration is None:
            self.remove_plugin_import_root(plugin_id)
            return
        for capability in registration.capabilities:
            self.capabilities.pop(
                (plugin_id, capability.capability_id), None
            )
        for tool in registration.tools:
            self.tool_registry.unregister(tool.definition.name)
        if self.plugin_manager.registry.contains(plugin_id):
            self.plugin_manager.unregister(plugin_id)
        self.remove_plugin_import_root(plugin_id)

    async def disable_started_plugin(
        self,
        plugin_id: str,
        disabled: set[str],
        code: str,
        message: str,
        *,
        level: Literal["warning", "error"] = "error",
    ) -> None:
        if plugin_id in self.required_plugin_ids:
            raise RequiredPluginError(message)
        runtime = self.plugin_manager.get_runtime(plugin_id)
        try:
            await runtime.stop(drain=False)
        except Exception as error:
            self.diagnostics.add(
                plugin_id,
                "disabled_plugin_cleanup_failed",
                str(error),
            )
        finally:
            self.disable_plugin(
                plugin_id, disabled, code, message, level=level
            )

    async def rollback(self, started: list[str]) -> None:
        if self.plugin_manager.is_running:
            try:
                await self.plugin_manager.stop(timeout=5, drain=False)
            except Exception:
                self.logger.exception("PluginManager rollback failed")
            self.clear_registries()
            self.remove_import_roots()
            return
        cleaned: set[str] = set()
        for plugin_id in reversed(started):
            try:
                await self.plugin_manager.get_runtime(plugin_id).stop(
                    drain=False
                )
                cleaned.add(plugin_id)
            except Exception:
                self.logger.exception(
                    "Plugin rollback failed: %s", plugin_id
                )
        for plugin_id, registration in reversed(
            tuple(self.registrations.items())
        ):
            if plugin_id in cleaned:
                continue
            try:
                if self.plugin_manager.registry.contains(plugin_id):
                    await self.plugin_manager.get_runtime(plugin_id).stop(
                        drain=False
                    )
                else:
                    await registration.plugin.stop()
            except Exception:
                self.logger.exception(
                    "Constructed Plugin rollback failed: %s", plugin_id
                )
        self.clear_registries()
        self.remove_import_roots()

    def clear_registries(self) -> None:
        if self.tool_registry.is_frozen:
            self.tool_registry.unfreeze()
        for registration in self.registrations.values():
            for tool in registration.tools:
                if self.tool_registry.get(tool.definition.name) is tool:
                    self.tool_registry.unregister(tool.definition.name)
        for plugin_id in reversed(
            tuple(self.plugin_manager.registry.plugin_ids())
        ):
            status = self.plugin_manager.registry.get_status(plugin_id)
            if status.value == "stopped":
                self.plugin_manager.unregister(plugin_id)
        self.capabilities.clear()
        self.capability_registry.clear()
        self.registrations.clear()

    def build_snapshot(
        self,
        workspace_path: Path,
        session_id: str,
        disabled: set[str],
    ) -> RuntimeGraphSnapshot:
        subscriptions: list[tuple[str, str]] = []
        for source_id in self.plugin_manager.registry.plugin_ids():
            subscriptions.extend(
                (source_id, subscriber_id)
                for subscriber_id in (
                    self.plugin_manager.registry.get_subscriber_ids(source_id)
                )
            )
        return RuntimeGraphSnapshot(
            workspace_path=str(workspace_path),
            session_id=session_id,
            plugins=tuple(
                RuntimePluginSnapshot(
                    plugin_id=plugin_id,
                    plugin_version=item.manifest.plugin_version,
                    manifest_hash=item.manifest_hash,
                    source=item.source,
                    state_scopes=tuple(item.manifest.state_scopes),
                    workspace_state_version=(
                        item.manifest.workspace_state_version
                    ),
                    session_state_version=item.manifest.session_state_version,
                )
                for plugin_id, item in self.discovered.items()
                if plugin_id in self.registrations
            ),
            disabled_plugin_ids=tuple(sorted(disabled)),
            capabilities=tuple(sorted(self.capabilities)),
            capability_bindings=tuple(
                sorted(
                    (
                        consumer_id,
                        self.provider_id(requirement.capability_id),
                        requirement.capability_id,
                    )
                    for consumer_id, item in self.discovered.items()
                    if consumer_id in self.registrations
                    for requirement in item.manifest.required_capabilities
                )
            ),
            tools=tuple(
                sorted(
                    (tool.definition.name, plugin_id)
                    for plugin_id, registration in self.registrations.items()
                    for tool in registration.tools
                )
            ),
            subscriptions=tuple(sorted(subscriptions)),
            start_order=tuple(self.registrations),
            stop_order=tuple(reversed(tuple(self.registrations))),
            diagnostics=tuple(
                (item.plugin_id, item.code, item.message, item.level)
                for item in self.diagnostics.items
            ),
        )

    def _acquire_import_root(self, import_root: str) -> None:
        if import_root in self._owned_import_roots:
            return
        with _IMPORT_ROOT_LOCK:
            count = _IMPORT_ROOT_REFERENCES.get(import_root, 0)
            if count == 0 and import_root not in sys.path:
                sys.path.insert(0, import_root)
            _IMPORT_ROOT_REFERENCES[import_root] = count + 1
        self._owned_import_roots.append(import_root)

    def remove_import_roots(self) -> None:
        self._contract_import_roots.clear()
        for import_root in tuple(self._owned_import_roots):
            self._release_import_root(import_root)

    def remove_plugin_import_root(self, plugin_id: str) -> None:
        item = self.discovered.get(plugin_id)
        if item is None or item.import_root is None:
            return
        import_root = str(item.import_root)
        if import_root in self._contract_import_roots:
            return
        self._release_import_root(import_root)

    def _release_import_root(self, import_root: str) -> None:
        if import_root not in self._owned_import_roots:
            return
        should_unload = False
        with _IMPORT_ROOT_LOCK:
            count = _IMPORT_ROOT_REFERENCES.get(import_root, 0)
            if count <= 1:
                _IMPORT_ROOT_REFERENCES.pop(import_root, None)
                try:
                    sys.path.remove(import_root)
                except ValueError:
                    pass
                should_unload = True
            else:
                _IMPORT_ROOT_REFERENCES[import_root] = count - 1
        self._owned_import_roots.remove(import_root)
        if should_unload:
            import_path = Path(import_root).resolve()
            for module_name, module in tuple(sys.modules.items()):
                module_file = getattr(module, "__file__", None)
                if not module_file:
                    continue
                try:
                    Path(module_file).resolve().relative_to(import_path)
                except (OSError, ValueError):
                    continue
                sys.modules.pop(module_name, None)
