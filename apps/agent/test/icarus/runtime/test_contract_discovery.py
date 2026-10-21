"""Contract discovery is distinct from creating and registering runtime providers."""

import asyncio
import importlib
import sys

import pytest

from icarus.core.events.catalog import builtin_event_catalog
from icarus.core.capabilities.catalog import builtin_capability_catalog
from icarus.runtime.host import PluginRuntimeHost
from icarus.runtime.resolver import RequiredPluginError
from apps.agent.test.icarus.runtime.contract_fixtures import (
    CONTRACTS, CONSUMER, PROVIDER, write_plugin,
)


def test_disabled_unknown_capability_does_not_abort_unrelated_host(tmp_path):
    root = tmp_path / "plugins"
    write_plugin(root, "healthy")
    write_plugin(root, "invalid", provided_capabilities=[{"capability_id": "unknown.lookup", "version": "1.0.0"}])
    async def run():
        host = PluginRuntimeHost(tmp_path, "isolation", plugin_dirs=(root,), required_plugin_ids=frozenset({"healthy"}))
        await host.start()
        try:
            assert host.plugin_manager.registry.plugin_ids() == ["healthy"]
            assert "invalid" in host.graph_snapshot.disabled_plugin_ids
        finally:
            await host.stop()
    asyncio.run(run())


@pytest.mark.parametrize("field, value, code", [
    ("published_events", ["unknown.changed"], "unknown_event"),
    ("consumed_events", ["unknown.changed"], "unknown_event"),
    ("provided_capabilities", [{"capability_id": "unknown.lookup", "version": "1.0.0"}], "unknown_capability"),
    ("required_capabilities", [{"capability_id": "unknown.lookup", "version_spec": ">=1"}], "unknown_capability"),
])
def test_unknown_contract_is_rejected_before_factory_import(tmp_path, field, value, code):
    root = tmp_path / "plugins"
    marker = tmp_path / "factory-imported"
    factory = f"from pathlib import Path\nPath({str(marker)!r}).touch()\n" + PROVIDER
    write_plugin(root, "unknown-source", published_events=["unknown.changed"])
    if field == "required_capabilities":
        write_plugin(root, "unknown-provider", provided_capabilities=[{"capability_id": "unknown.lookup", "version": "1.0.0"}])
    write_plugin(root, "invalid", factory=factory, **{field: value})
    async def run():
        host = PluginRuntimeHost(tmp_path, "early", plugin_dirs=(root,))
        await host.start()
        try:
            assert not marker.exists()
            assert "invalid.factory" not in sys.modules
            assert any(item.code == code for item in host.diagnostics.for_plugin("invalid"))
        finally:
            await host.stop()
    asyncio.run(run())


def test_required_unknown_contract_fails_without_running_factory(tmp_path):
    marker = tmp_path / "factory-imported"
    root = tmp_path / "plugins"
    write_plugin(root, "invalid", factory=f"from pathlib import Path\nPath({str(marker)!r}).touch()\n",
                 published_events=["unknown.changed"])
    async def run():
        host = PluginRuntimeHost(tmp_path, "required", plugin_dirs=(root,), required_plugin_ids=frozenset({"invalid"}))
        with pytest.raises(RequiredPluginError):
            await host.start()
        assert host.status == "failed"
        assert not marker.exists()
    asyncio.run(run())


def test_external_plugins_discover_their_own_event_and_capability_contracts(tmp_path):
    root = tmp_path / "plugins"
    write_plugin(root, "demo-provider", factory=PROVIDER, contract_source=CONTRACTS,
                 contracts="demo_provider.contracts",
                 provided_capabilities=[{"capability_id": "extension.lookup", "version": "1.0.0"}],
                 published_events=["extension.changed"])
    write_plugin(root, "demo-consumer", factory=CONSUMER,
                 contracts="demo_provider.contracts",
                 required_capabilities=[{"capability_id": "extension.lookup", "version_spec": ">=1,<2"}],
                 consumed_events=["extension.changed"])
    async def run():
        host = PluginRuntimeHost(tmp_path, "extension", plugin_dirs=(root,),
                                 required_plugin_ids=frozenset({"demo-provider", "demo-consumer"}))
        await host.start()
        try:
            provider = host.get_plugin("demo-provider")
            consumer = host.get_plugin("demo-consumer")
            changed = host.graph.event_catalog.resolve("extension.changed")
            await provider.publish(changed(task_id="task", value="notice"))
            await asyncio.wait_for(consumer.finished.wait(), 1)
            assert consumer.responses == [("demo-provider", "notice", "provider-answer")]
            assert host.graph.capability_registry.provider_for("extension.lookup") == "demo-provider"
        finally:
            await host.stop()
        assert str(root / "demo-provider") not in sys.path
        assert str(root / "demo-consumer") not in sys.path
    asyncio.run(run())


def test_host_accepts_catalogs_without_mutating_callers(tmp_path):
    events = builtin_event_catalog()
    capabilities = builtin_capability_catalog()
    host = PluginRuntimeHost(tmp_path, "injected", event_catalog=events, capability_catalog=capabilities)
    assert host.graph.event_catalog is not events
    assert host.graph.capability_catalog is not capabilities
    assert host.graph.event_catalog.resolve("input.user_input") is events.resolve("input.user_input")
    assert host.graph.capability_catalog.resolve("state.store") is capabilities.resolve("state.store")


def test_two_hosts_keep_shared_external_contract_identity_until_last_stop(tmp_path):
    root = tmp_path / "plugins"
    write_plugin(root, "demo-provider", factory=PROVIDER, contract_source=CONTRACTS,
                 contracts="demo_provider.contracts",
                 provided_capabilities=[{"capability_id": "extension.lookup", "version": "1.0.0"}],
                 published_events=["extension.changed"])
    write_plugin(root, "demo-consumer", factory=CONSUMER, contracts="demo_provider.contracts",
                 required_capabilities=[{"capability_id": "extension.lookup", "version_spec": ">=1,<2"}],
                 consumed_events=["extension.changed"])
    async def run():
        first = PluginRuntimeHost(tmp_path, "first", plugin_dirs=(root,))
        second = PluginRuntimeHost(tmp_path, "second", plugin_dirs=(root,))
        await first.start()
        try:
            await second.start()
            changed = first.graph.event_catalog.resolve("extension.changed")
            assert second.graph.event_catalog.resolve("extension.changed") is changed
            await first.stop()
            provider = second.get_plugin("demo-provider")
            consumer = second.get_plugin("demo-consumer")
            assert importlib.import_module("demo_provider.contracts").Changed is changed
            await provider.publish(changed(task_id="task", value="after-first-stop"))
            await asyncio.wait_for(consumer.finished.wait(), 1)
            assert consumer.responses == [("demo-provider", "after-first-stop", "provider-answer")]
        finally:
            await first.stop()
            await second.stop()
        assert "demo_provider.contracts" not in sys.modules
        assert str(root / "demo-provider") not in sys.path
    asyncio.run(run())
