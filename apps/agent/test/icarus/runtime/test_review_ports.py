"""Startup rejects invalid host state ports and catalog impersonation."""

from pathlib import Path
import pytest

from icarus.core.capabilities.catalog import StateStoreAccess
from icarus.core.manifest import PluginManifest
from icarus.core.plugin import BasePlugin, PluginContractError
from icarus.core.registration import PluginRegistration, ProvidedCapability
from icarus.runtime.discovery import DiscoveredPlugin
from icarus.runtime.host import PluginRuntimeHost


def state_manifest():
    return PluginManifest.model_validate({
        "schema_version": 2, "plugin_id": "alternate-store", "plugin_version": "1.0.0",
        "entrypoint": "alternate.factory:create_plugin", "tier": "base", "python_requires": [],
        "required_capabilities": [], "provided_capabilities": [{"capability_id": "state.store", "version": "1.0.0"}],
        "provided_tools": [], "published_events": [], "consumed_events": [], "state_scopes": [],
    })


def test_invalid_required_state_port_is_rejected_before_ready(tmp_path):
    class Plugin(BasePlugin):
        provided_capabilities = (StateStoreAccess,)
    host = PluginRuntimeHost(tmp_path, "invalid")
    item = DiscoveredPlugin(state_manifest(), "hash", "test", False)
    registration = PluginRegistration(Plugin("alternate-store"), capabilities=(ProvidedCapability("state.store", "1.0.0", object()),))
    with pytest.raises((TypeError, ValueError), match="port"):
        host.graph._validate_registration(item, registration)


def test_plugin_capability_class_must_be_the_catalog_contract():
    class FakeState(StateStoreAccess):
        pass
    class Plugin(BasePlugin):
        provided_capabilities = (FakeState,)
    from icarus.core.capabilities.catalog import builtin_capability_catalog
    with pytest.raises(PluginContractError, match="catalog"):
        Plugin("alternate-store").validate_manifest(state_manifest(), set(), builtin_capability_catalog())
