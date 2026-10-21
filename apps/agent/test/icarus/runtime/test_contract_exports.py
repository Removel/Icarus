"""Declarative exports are validated atomically before either catalog changes."""

from types import ModuleType
import pytest

from icarus.core.capabilities.catalog import builtin_capability_catalog
from icarus.core.events.catalog import builtin_event_catalog
from icarus.runtime.contracts import ContractLoader
from icarus.runtime.diagnostics import RuntimeDiagnostics
from icarus.runtime.discovery import DiscoveredPlugin
from icarus.core.manifest import PluginManifest


def item(name, module):
    return DiscoveredPlugin(PluginManifest.model_validate({
        "schema_version": 2, "plugin_id": name, "plugin_version": "1.0.0",
        "entrypoint": f"{name}.factory:create_plugin", "contracts": module,
        "python_requires": [], "required_capabilities": [], "provided_capabilities": [],
        "provided_tools": [], "published_events": [], "consumed_events": [], "state_scopes": [],
    }), name, "test", False)


def test_reexporting_identical_types_from_distinct_modules_is_idempotent():
    from icarus.core.events import Event
    class Changed(Event): pass
    first, second = ModuleType("first"), ModuleType("second")
    for module in (first, second):
        module.EVENTS = {"shared.changed": Changed}
        module.CAPABILITIES = ()
    events, capabilities = builtin_event_catalog(), builtin_capability_catalog()
    loader = ContractLoader(events, capabilities, {"first": first, "second": second}.__getitem__, RuntimeDiagnostics())
    assert loader.load((item("first", "first"), item("second", "second"))) == frozenset()
    assert events.resolve("shared.changed") is Changed


def test_contract_module_without_declarative_exports_is_rejected():
    empty = ModuleType("empty")
    events, capabilities = builtin_event_catalog(), builtin_capability_catalog()
    diagnostics = RuntimeDiagnostics()
    loader = ContractLoader(events, capabilities, lambda name: empty, diagnostics)
    assert loader.load((item("empty", "empty"),)) == {"empty"}
    assert diagnostics.for_plugin("empty")[0].code == "contract_load_failed"


def test_every_claimant_is_rejected_when_one_reexport_conflicts():
    from icarus.core.events import Event
    class Original(Event): pass
    class Conflicting(Event): pass
    modules = {name: ModuleType(name) for name in ("a", "b", "c")}
    for name, module in modules.items():
        module.EVENTS = {"shared.changed": Conflicting if name == "b" else Original}
        module.CAPABILITIES = ()
    events, capabilities = builtin_event_catalog(), builtin_capability_catalog()
    loader = ContractLoader(events, capabilities, modules.__getitem__, RuntimeDiagnostics())
    assert loader.load(tuple(item(name, name) for name in modules)) == {"a", "b", "c"}
    with pytest.raises(KeyError):
        events.resolve("shared.changed")
