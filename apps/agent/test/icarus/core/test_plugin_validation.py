"""Manifest validation rejects handlers/tools that are not declared by the plugin."""

import asyncio
from dataclasses import dataclass

import pytest
from icarus.core.events import Event
from icarus.core.plugin import BasePlugin, handles, PluginContractError
from icarus.core.manifest import PluginManifest


@dataclass(frozen=True, kw_only=True)
class Unlisted(Event):
    pass


def test_handler_manifest_mismatch_is_rejected():
    class Plugin(BasePlugin):
        @handles(Unlisted)
        async def event(self, source, event):
            pass
    manifest = PluginManifest.model_validate({
        "schema_version": 2, "plugin_id": "sample", "plugin_version": "1.0.0",
        "entrypoint": "sample.factory:create_plugin", "python_requires": [],
        "required_capabilities": [], "provided_capabilities": [], "provided_tools": [],
        "published_events": [], "consumed_events": [], "state_scopes": [],
    })
    plugin = Plugin("sample")
    assert hasattr(plugin, "validate_manifest"), "handler/manifest agreement is not enforced"
    with pytest.raises(PluginContractError):
        plugin.validate_manifest(manifest, {})
