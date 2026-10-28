"""Manifest-loaded plugins cannot bypass handler declarations through consume overrides."""

import pytest
from icarus.core.plugin import BasePlugin, PluginContractError
from icarus.core.manifest import PluginManifest


def test_manifest_plugin_rejects_fallback_consume_override():
    class Plugin(BasePlugin):
        async def consume(self, source, event):
            pass
    manifest = PluginManifest.model_validate({
        "schema_version": 2, "plugin_id": "sample", "plugin_version": "1.0.0",
        "entrypoint": "sample.factory:create_plugin", "python_requires": [],
        "required_capabilities": [], "provided_capabilities": [], "provided_tools": [],
        "published_events": [], "consumed_events": [], "state_scopes": [],
    })
    with pytest.raises(PluginContractError, match="consume"):
        Plugin("sample").validate_manifest(manifest, set())
