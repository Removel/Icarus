"""Missing required user config fails before executing the plugin entrypoint module."""

import asyncio
import sys
import pytest
from icarus.core.manifest import PluginManifest
from icarus.runtime.discovery import DiscoveredPlugin
from icarus.runtime.host import PluginRuntimeHost


def test_required_config_is_checked_before_factory_import(tmp_path):
    package = tmp_path / "test_plugin"
    package.mkdir(); (package / "__init__.py").write_text("")
    marker = tmp_path / "imported"
    (package / "factory.py").write_text(f"from pathlib import Path\nPath({str(marker)!r}).touch()\ndef create_plugin(**kwargs): pass\n")
    manifest = PluginManifest.model_validate({
        "schema_version": 2, "plugin_id": "test-plugin", "plugin_version": "1.0.0",
        "entrypoint": "test_plugin.factory:create_plugin", "required_config": ["endpoint"],
        "python_requires": [], "required_capabilities": [], "provided_capabilities": [],
        "provided_tools": [], "published_events": [], "consumed_events": [], "state_scopes": [],
    })
    host = PluginRuntimeHost(tmp_path, "config")
    item = DiscoveredPlugin(manifest, "hash", "test", False, tmp_path)
    async def run():
        try:
            with pytest.raises(ValueError, match="Missing required"):
                await host.graph._call_factory(item, {})
        finally:
            host.graph.remove_import_roots()
    asyncio.run(run())
    assert not marker.exists()
