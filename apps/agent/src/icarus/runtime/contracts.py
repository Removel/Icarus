"""Trusted extension contract exports, loaded before plugin implementation factories."""

from collections.abc import Callable, Mapping
import inspect
import re
from types import ModuleType

from packaging.version import Version

from icarus.core.capabilities.base import CapabilityRequest, CapabilityResponse
from icarus.core.capabilities.catalog import CapabilityCatalog, CapabilityContract
from icarus.core.events import Event
from icarus.core.events.catalog import EventCatalog
from icarus.runtime.diagnostics import RuntimeDiagnostics
from icarus.runtime.discovery import DiscoveredPlugin


_CONTRACT_ID = re.compile(r"^[a-z][a-z0-9._-]*$")


class ContractLoader:
    """Load declarative type exports; never construct plugins or register providers."""

    def __init__(self, events: EventCatalog, capabilities: CapabilityCatalog,
                 import_module: Callable[[str], ModuleType], diagnostics: RuntimeDiagnostics) -> None:
        self.events = events
        self.capabilities = capabilities
        self.import_module = import_module
        self.diagnostics = diagnostics
        self.definitions: set[type] = set()

    def load(self, plugins: tuple[DiscoveredPlugin, ...]) -> frozenset[str]:
        owners: dict[str, list[str]] = {}
        for item in plugins:
            if item.manifest.contracts is not None:
                owners.setdefault(item.manifest.contracts, []).append(item.manifest.plugin_id)
        exports: dict[str, tuple[dict[str, type[Event]], tuple[type[CapabilityContract], ...]]] = {}
        rejected: dict[str, tuple[str, str]] = {}
        for name in sorted(owners):
            try:
                exports[name] = self._read(self.import_module(name))
            except Exception as error:
                rejected[name] = ("contract_load_failed", f"Cannot load contracts {name}: {error}")

        event_ids: dict[str, tuple[type[Event], set[str], bool]] = {}
        event_types: dict[type[Event], tuple[str, set[str], bool]] = {}
        capability_ids: dict[str, tuple[type[CapabilityContract], set[str], bool]] = {}
        for name, (events, capabilities) in exports.items():
            for event_id, event_type in events.items():
                try:
                    existing = self.events.resolve(event_id)
                except KeyError:
                    existing = None
                if existing is not None and existing is not event_type:
                    rejected[name] = ("contract_conflict", f"Event ID conflicts with catalog: {event_id}")
                try:
                    existing_id = self.events.id_for(event_type)
                except KeyError:
                    existing_id = event_id
                if existing_id != event_id:
                    rejected[name] = ("contract_conflict", f"Event type has another ID: {event_id}")
                self._claim(event_ids, event_id, event_type, name, rejected)
                self._claim(event_types, event_type, event_id, name, rejected)
            for capability in capabilities:
                try:
                    existing_capability = self.capabilities.resolve(capability.capability_id)
                except KeyError:
                    existing_capability = None
                if existing_capability is not None and existing_capability is not capability:
                    rejected[name] = ("contract_conflict", f"Capability conflicts with catalog: {capability.capability_id}")
                self._claim(capability_ids, capability.capability_id, capability, name, rejected)

        # Apply whole validated modules, not partially registered bundles.
        for name, (events, capabilities) in exports.items():
            if name in rejected:
                continue
            self.definitions.update(events.values())
            for capability in capabilities:
                self.definitions.add(capability)
                if capability.access_mode == "event":
                    self.definitions.update((capability.request_type, capability.response_type))
                elif capability.port_type is not None:
                    self.definitions.add(capability.port_type)
            for event_id, event_type in events.items():
                try:
                    self.events.resolve(event_id)
                except KeyError:
                    self.events.register(event_id, event_type)
            for capability in capabilities:
                try:
                    self.capabilities.resolve(capability.capability_id)
                except KeyError:
                    self.capabilities.register(capability)
        disabled: set[str] = set()
        for name, (code, message) in rejected.items():
            for plugin_id in owners[name]:
                disabled.add(plugin_id)
                self.diagnostics.add(plugin_id, code, message)
        return frozenset(disabled)

    @staticmethod
    def _claim(claims, key, value, module, rejected) -> None:
        previous = claims.get(key)
        if previous is None:
            claims[key] = (value, {module}, False)
            return
        expected, modules, conflicted = previous
        modules.add(module)
        conflicted = conflicted or (expected is not value and expected != value)
        claims[key] = (expected, modules, conflicted)
        if conflicted:
            for owner in modules:
                rejected[owner] = ("contract_conflict", f"Conflicting contract definition: {key}")

    @staticmethod
    def _read(module: ModuleType) -> tuple[dict[str, type[Event]], tuple[type[CapabilityContract], ...]]:
        events = getattr(module, "EVENTS")
        capabilities = getattr(module, "CAPABILITIES")
        if not isinstance(events, Mapping) or not isinstance(capabilities, tuple):
            raise TypeError("Contracts must export EVENTS as a mapping and CAPABILITIES as a tuple")
        checked_events: dict[str, type[Event]] = {}
        for event_id, event_type in events.items():
            if not isinstance(event_id, str) or not _CONTRACT_ID.fullmatch(event_id) or "." not in event_id:
                raise ValueError("Event IDs must be stable namespaced identifiers")
            if not inspect.isclass(event_type) or not issubclass(event_type, Event):
                raise TypeError(f"Event definition must be an Event type: {event_id}")
            if issubclass(event_type, (CapabilityRequest, CapabilityResponse)):
                raise TypeError("Capability request/response types cannot be exported as business events")
            checked_events[event_id] = event_type
        seen: set[str] = set()
        checked_capabilities: list[type[CapabilityContract]] = []
        for capability in capabilities:
            if not inspect.isclass(capability) or not issubclass(capability, CapabilityContract):
                raise TypeError("Capabilities must be CapabilityContract classes")
            capability_id = capability.capability_id
            if not isinstance(capability_id, str) or not _CONTRACT_ID.fullmatch(capability_id):
                raise ValueError("Invalid capability ID")
            if capability_id in seen:
                raise ValueError(f"Duplicate capability export: {capability_id}")
            seen.add(capability_id)
            Version(capability.version)
            if capability.access_mode == "event":
                request, response = capability.request_type, capability.response_type
                if not inspect.isclass(request) or not issubclass(request, CapabilityRequest):
                    raise TypeError("Event capability request must derive from CapabilityRequest")
                if not inspect.isclass(response) or not issubclass(response, CapabilityResponse):
                    raise TypeError("Event capability response must derive from CapabilityResponse")
                if request.capability_id != capability_id or request.response_type is not response:
                    raise ValueError("Capability request/response bindings must match their contract")
            elif capability.access_mode == "host":
                if not getattr(capability.port_type, "_is_runtime_protocol", False):
                    raise TypeError("Host capability must declare a runtime-checkable Protocol port")
            else:
                raise ValueError("Unknown capability access mode")
            checked_capabilities.append(capability)
        return checked_events, tuple(checked_capabilities)
