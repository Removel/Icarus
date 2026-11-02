"""Trusted builtin namespace groups remain independent leaf plugins."""
import json
from importlib import invalidate_caches

from icarus.runtime.discovery import PluginManifestDiscovery


def _manifest(plugin_id, module):
    return dict(schema_version=2, plugin_id=plugin_id, plugin_version="1.0.0",
                entrypoint=module + ":create_plugin", tier="external", python_requires=[],
                required_capabilities=[], provided_capabilities=[], provided_tools=[],
                published_events=[], consumed_events=[], state_scopes=[])


def test_builtin_namespace_groups_discover_leaf_manifests_without_importing_factories(tmp_path, monkeypatch):
    root = tmp_path / "grouped_plugins"
    root.mkdir()
    (root / "__init__.py").write_text("")
    for group, leaf in (("protocols", "qq_protocol"), ("rule_capabilities", "observation_query")):
        folder = root / group / leaf
        folder.mkdir(parents=True)
        (folder / "manifest.json").write_text(json.dumps(_manifest(leaf.replace("_", "-"), "never_imported.factory")))
        (folder / "factory.py").write_text("raise AssertionError('discovery imported factory')")
    monkeypatch.syspath_prepend(str(tmp_path))
    invalidate_caches()
    result = PluginManifestDiscovery(builtin_package="grouped_plugins").discover()
    assert [p.manifest.plugin_id for p in result.plugins] == ["observation-query", "qq-protocol"]
    assert all(p.built_in and p.import_root is None for p in result.plugins)
    assert not result.diagnostics.items


def test_builtin_leaf_stops_recursion_and_duplicates_keep_diagnostics(tmp_path, monkeypatch):
    root = tmp_path / "leaf_plugins"
    root.mkdir()
    (root / "__init__.py").write_text("")
    for path in ("group/duplicate", "other/duplicate", "single", "single/hidden"):
        folder = root / path
        folder.mkdir(parents=True, exist_ok=True)
        leaf = folder.name
        (folder / "manifest.json").write_text(json.dumps(_manifest(leaf, "factory.module")))
    monkeypatch.syspath_prepend(str(tmp_path))
    invalidate_caches()
    result = PluginManifestDiscovery(builtin_package="leaf_plugins").discover()
    assert [p.manifest.plugin_id for p in result.plugins] == ["single"]
    assert any(d.code == "duplicate_builtin_plugin" for d in result.diagnostics.items)


def test_external_discovery_does_not_recurse_into_namespace_groups(tmp_path):
    folder = tmp_path / "protocols" / "external"
    folder.mkdir(parents=True)
    (folder / "manifest.json").write_text(json.dumps(_manifest("external", "external.factory")))
    assert PluginManifestDiscovery(plugin_dirs=(tmp_path,)).discover().plugins == ()
