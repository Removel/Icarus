"""Shared type definitions load once and never overwrite existing contracts."""

import asyncio
import importlib
import sys

import pytest

from icarus.core.events.catalog import builtin_event_catalog
from icarus.core.capabilities.catalog import builtin_capability_catalog
from icarus.runtime.discovery import PluginManifestDiscovery
from icarus.runtime.host import PluginRuntimeHost
from icarus.runtime.resolver import RequiredPluginError
from apps.agent.test.icarus.runtime.contract_fixtures import CONTRACTS, EMPTY, write_plugin


def test_json_discovery_does_not_import_contracts_or_factory(tmp_path):
    marker = tmp_path / "imported"
    root = tmp_path / "plugins"
    write_plugin(root, "lazy", contracts="lazy.contracts",
                 contract_source=f"from pathlib import Path\nPath({str(marker)!r}).touch()\n" + CONTRACTS,
                 factory=f"from pathlib import Path\nPath({str(marker)!r}).touch()\n" + EMPTY)
    result = PluginManifestDiscovery((root,)).discover()
    assert len(result.plugins) == 1
    assert result.plugins[0].manifest.contracts == "lazy.contracts"
    assert not marker.exists()
    assert "lazy.contracts" not in sys.modules


def test_shared_contract_exports_are_read_once_per_host(tmp_path):
    root = tmp_path / "plugins"
    marker = tmp_path / "export-reads"
    exports = f'''from pathlib import Path
from icarus.core.events import Event
class Changed(Event):
    pass
def __getattr__(name):
    if name == "EVENTS":
        with Path({str(marker)!r}).open("a") as file:
            file.write("read\\n")
        return {{"shared.changed": Changed}}
    if name == "CAPABILITIES":
        return ()
    raise AttributeError(name)
'''
    write_plugin(root, "shared-owner", contracts="shared_owner.contracts", contract_source=exports)
    write_plugin(root, "shared-user", contracts="shared_owner.contracts")
    async def run():
        host = PluginRuntimeHost(tmp_path, "shared", plugin_dirs=(root,))
        await host.start()
        try:
            assert set(host.plugin_manager.registry.plugin_ids()) == {"shared-owner", "shared-user"}
            assert marker.read_text().splitlines() == ["read"]
        finally:
            await host.stop()
    asyncio.run(run())


@pytest.mark.parametrize("source", [
    "EVENTS = {'invalid.event': object}\nCAPABILITIES = ()\n",
    "EVENTS = {}\nCAPABILITIES = (object,)\n",
    "EVENTS = {}\nCAPABILITIES = []\n",
    CONTRACTS.replace('response_type = Reply\n\nEVENTS', 'response_type = object\n\nEVENTS'),
    CONTRACTS + '\nEVENTS["extension.alias"] = Changed\n',
])
def test_invalid_contract_bundle_is_atomic_and_does_not_execute_factory(tmp_path, source):
    root = tmp_path / "plugins"
    marker = tmp_path / "factory-imported"
    write_plugin(root, "malformed", contracts="malformed.contracts", contract_source=source,
                 factory=f"from pathlib import Path\nPath({str(marker)!r}).touch()\n" + EMPTY)
    async def run():
        host = PluginRuntimeHost(tmp_path, "malformed", plugin_dirs=(root,))
        await host.start()
        try:
            assert not marker.exists()
            assert host.plugin_manager.registry.plugin_ids() == []
            with pytest.raises(KeyError):
                host.graph.event_catalog.resolve("extension.changed")
            with pytest.raises(KeyError):
                host.graph.capability_catalog.resolve("extension.lookup")
        finally:
            await host.stop()
        assert str(root / "malformed") not in sys.path
    asyncio.run(run())


def test_all_conflicting_contract_modules_are_rejected_not_first_wins(tmp_path):
    root = tmp_path / "plugins"
    for name in ("first", "second", "third"):
        write_plugin(root, name, contracts=f"{name}.contracts",
                     contract_source=CONTRACTS.replace("extension.changed", "conflict.changed"))
    async def run():
        host = PluginRuntimeHost(tmp_path, "conflicts", plugin_dirs=(root,))
        await host.start()
        try:
            assert host.plugin_manager.registry.plugin_ids() == []
            for name in ("first", "second", "third"):
                assert any(item.code == "contract_conflict" for item in host.diagnostics.for_plugin(name))
            with pytest.raises(KeyError):
                host.graph.event_catalog.resolve("conflict.changed")
        finally:
            await host.stop()
    asyncio.run(run())


def test_contract_failure_cascades_before_dependent_factory_import(tmp_path):
    root = tmp_path / "plugins"
    marker = tmp_path / "dependent-imported"
    write_plugin(root, "bad-owner", contracts="bad_owner.contracts",
                 contract_source="raise RuntimeError('broken definitions')\n",
                 provided_capabilities=[{"capability_id": "extension.lookup", "version": "1.0.0"}])
    write_plugin(root, "dependent", required_capabilities=[{"capability_id": "extension.lookup", "version_spec": ">=1"}],
                 factory=f"from pathlib import Path\nPath({str(marker)!r}).touch()\n" + EMPTY)
    async def run():
        host = PluginRuntimeHost(tmp_path, "cascade", plugin_dirs=(root,))
        await host.start()
        try:
            assert not marker.exists()
            assert host.plugin_manager.registry.plugin_ids() == []
            assert set(host.graph_snapshot.disabled_plugin_ids) == {"bad-owner", "dependent"}
        finally:
            await host.stop()
    asyncio.run(run())


def test_contract_import_failure_rolls_back_all_external_roots_for_required_plugin(tmp_path):
    root = tmp_path / "plugins"
    write_plugin(root, "bad-owner", tier="base", contracts="bad_owner.contracts",
                 contract_source="raise RuntimeError('broken definitions')\n")
    async def run():
        host = PluginRuntimeHost(tmp_path, "required", plugin_dirs=(root,))
        with pytest.raises(RequiredPluginError):
            await host.start()
        assert str(root / "bad-owner") not in sys.path
        assert "bad_owner" not in sys.modules
    asyncio.run(run())


def test_failed_factory_does_not_unload_shared_contracts_used_by_enabled_plugin(tmp_path):
    root = tmp_path / "plugins"
    write_plugin(root, "definitions", contracts="definitions.contracts", contract_source=CONTRACTS,
                 factory="def create_plugin(**kwargs): raise RuntimeError('optional implementation failed')\n")
    factory = '''from definitions.contracts import Changed
from icarus.core.plugin import BasePlugin, handles
from icarus.core.registration import PluginRegistration
class User(BasePlugin):
    @handles(Changed)
    async def changed(self, source, event):
        pass
def create_plugin(plugin_id, **kwargs):
    return PluginRegistration(User(plugin_id))
'''
    write_plugin(root, "definitions-user", contracts="definitions.contracts", factory=factory,
                 published_events=["extension.changed"], consumed_events=["extension.changed"])
    async def run():
        host = PluginRuntimeHost(tmp_path, "shared-types", plugin_dirs=(root,))
        await host.start()
        try:
            assert host.plugin_manager.registry.plugin_ids() == ["definitions-user"]
            contract = host.graph.event_catalog.resolve("extension.changed")
            assert contract is importlib.import_module("definitions.contracts").Changed
        finally:
            await host.stop()
        assert str(root / "definitions") not in sys.path
        assert "definitions.contracts" not in sys.modules
    asyncio.run(run())


def test_python_dependency_gate_prevents_contract_module_import(tmp_path):
    root = tmp_path / "plugins"
    marker = tmp_path / "contracts-imported"
    write_plugin(root, "missing-dependency", contracts="missing_dependency.contracts",
                 python_requires=["definitely-missing-icarus-contract-test-package>=1"],
                 contract_source=f"from pathlib import Path\nPath({str(marker)!r}).touch()\n" + CONTRACTS)
    async def run():
        host = PluginRuntimeHost(tmp_path, "prerequisites", plugin_dirs=(root,))
        await host.start()
        try:
            assert not marker.exists()
            assert host.plugin_manager.registry.plugin_ids() == []
            assert any(item.code == "python_dependency_unavailable" for item in host.diagnostics.for_plugin("missing-dependency"))
        finally:
            await host.stop()
    asyncio.run(run())


def test_dependency_rejected_plugin_does_not_import_contracts_or_poison_required_owner(tmp_path):
    root = tmp_path / "plugins"
    marker = tmp_path / "unavailable-contracts-imported"
    write_plugin(root, "healthy-owner", tier="base", contracts="healthy_owner.contracts",
                 contract_source=CONTRACTS)
    write_plugin(root, "unavailable", contracts="unavailable.contracts",
                 required_capabilities=[{"capability_id": "state.store", "version_spec": ">=1,<2"}],
                 contract_source=f"from pathlib import Path\nPath({str(marker)!r}).touch()\n" + CONTRACTS)
    async def run():
        host = PluginRuntimeHost(tmp_path, "dependency-gate", plugin_dirs=(root,))
        await host.start()
        try:
            assert host.plugin_manager.registry.plugin_ids() == ["healthy-owner"]
            assert not marker.exists()
            assert host.graph.event_catalog.resolve("extension.changed").__module__ == "healthy_owner.contracts"
            assert any(item.code == "required_capability_unavailable" for item in host.diagnostics.for_plugin("unavailable"))
        finally:
            await host.stop()
    asyncio.run(run())


def test_reexported_definition_root_survives_its_owner_factory_failure(tmp_path):
    root = tmp_path / "plugins"
    write_plugin(root, "definitions", contract_source=CONTRACTS,
                 factory="def create_plugin(**kwargs): raise RuntimeError('optional implementation failed')\n")
    factory = '''from definitions.contracts import Changed
from icarus.core.plugin import BasePlugin, handles
from icarus.core.registration import PluginRegistration
class User(BasePlugin):
    @handles(Changed)
    async def changed(self, source, event):
        pass
def create_plugin(plugin_id, **kwargs):
    return PluginRegistration(User(plugin_id))
'''
    write_plugin(root, "reexport-user", contracts="reexport_user.contracts", factory=factory,
                 contract_source="from definitions.contracts import Changed\nEVENTS = {'extension.changed': Changed}\nCAPABILITIES = ()\n",
                 published_events=["extension.changed"], consumed_events=["extension.changed"])
    async def run():
        host = PluginRuntimeHost(tmp_path, "reexport-types", plugin_dirs=(root,))
        await host.start()
        try:
            assert host.plugin_manager.registry.plugin_ids() == ["reexport-user"]
            contract = host.graph.event_catalog.resolve("extension.changed")
            assert contract is importlib.import_module("definitions.contracts").Changed
        finally:
            await host.stop()
        assert "definitions.contracts" not in sys.modules
        assert str(root / "definitions") not in sys.path
    asyncio.run(run())


def test_extension_loading_does_not_mutate_injected_catalogs_or_other_hosts(tmp_path):
    root = tmp_path / "plugins"
    write_plugin(root, "extension", contracts="extension.contracts", contract_source=CONTRACTS)
    events, capabilities = builtin_event_catalog(), builtin_capability_catalog()
    async def run():
        host = PluginRuntimeHost(tmp_path, "extended", plugin_dirs=(root,), event_catalog=events, capability_catalog=capabilities)
        await host.start()
        try:
            assert host.graph.event_catalog.resolve("extension.changed")
            other = PluginRuntimeHost(tmp_path, "plain", event_catalog=events, capability_catalog=capabilities)
            with pytest.raises(KeyError):
                other.graph.event_catalog.resolve("extension.changed")
            with pytest.raises(KeyError):
                events.resolve("extension.changed")
            with pytest.raises(KeyError):
                capabilities.resolve("extension.lookup")
        finally:
            await host.stop()
    asyncio.run(run())
