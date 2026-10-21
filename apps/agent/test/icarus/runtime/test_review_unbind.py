"""Disabled providers and rolled-back managers must not retain live bindings."""

import pytest
from icarus.core.plugin import BasePlugin
from icarus.core.registration import PluginRegistration, ProvidedCapability
from icarus.runtime.host import PluginRuntimeHost
from icarus.runtime.manager import PluginManager


def test_disabling_provider_revokes_host_port(tmp_path):
    host = PluginRuntimeHost(tmp_path, "bindings")
    provider = BasePlugin("store")
    value = object()
    host.graph.registrations["store"] = PluginRegistration(provider, (ProvidedCapability("state.store", "1.0.0", value),))
    host.graph.capabilities[("store", "state.store")] = value
    host.graph.capability_registry.bind("state.store", "1.0.0", "store", value)
    host.graph.disable_plugin("store", set(), "failure", "failure")
    with pytest.raises(KeyError):
        host.graph.capability_registry.get_host_port("state.store")


def test_unregister_provider_clears_manager_binding():
    manager = PluginManager()
    manager.register(BasePlugin("provider"))
    manager.bind_capability("conversation.read", "provider", required_by={"caller"})
    manager.unregister("provider")
    manager.register(BasePlugin("replacement"))
    manager.bind_capability("conversation.read", "replacement", required_by={"caller"})
    assert manager._capability_providers["conversation.read"] == "replacement"
