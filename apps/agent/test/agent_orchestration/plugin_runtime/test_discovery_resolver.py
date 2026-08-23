import json

import pytest

from apps.agent.src.agent_orchestration.plugin_runtime.diagnostics import (
    RuntimeDiagnostics,
)
from apps.agent.src.agent_orchestration.plugin_runtime.discovery import (
    DiscoveredPlugin,
    PluginManifestDiscovery,
)
from apps.agent.src.agent_orchestration.plugin_runtime.manifest import (
    PluginManifest,
)
from apps.agent.src.agent_orchestration.plugin_runtime.resolver import (
    RequiredPluginError,
    resolve_plugins,
)


def manifest(plugin_id, **changes):
    data = {
        "schema_version": 1,
        "plugin_id": plugin_id,
        "plugin_version": "1.0.0",
        "entrypoint": (
            f"{plugin_id.replace('-', '_')}.factory:create_plugin"
        ),
        "python_requires": [],
        "required_capabilities": [],
        "provided_capabilities": [],
        "provided_tools": [],
        "published_events": [],
        "consumed_events": [],
        "state_scopes": [],
    }
    data.update(changes)
    return PluginManifest.model_validate(data)


def discovered(plugin_id, **changes):
    return DiscoveredPlugin(
        manifest=manifest(plugin_id, **changes),
        manifest_hash=plugin_id,
        source=f"/{plugin_id}/manifest.json",
        built_in=False,
    )


def write_manifest(root, plugin_id, **changes):
    directory = root / plugin_id
    directory.mkdir(parents=True)
    data = manifest(plugin_id, **changes).model_dump(mode="json")
    (directory / "manifest.json").write_text(
        json.dumps(data),
        encoding="utf-8",
    )


def test_discovery只扫描显式目录直接子目录并去重路径(tmp_path):
    root = tmp_path / "plugins"
    write_manifest(root, "sample")
    nested = root / "container" / "nested"
    nested.mkdir(parents=True)
    (nested / "manifest.json").write_text("{}", encoding="utf-8")

    result = PluginManifestDiscovery(
        [root, root / ".." / "plugins"],
        builtin_package="apps.agent.test.agent_orchestration.plugin_runtime.empty_plugins",
    ).discover()

    assert [item.manifest.plugin_id for item in result.plugins] == ["sample"]


def test_resolver按能力依赖排序并级联禁用可选plugin():
    provider = discovered(
        "provider",
        provided_capabilities=[
            {"capability_id": "sample-api", "version": "1.2.0"}
        ],
    )
    consumer = discovered(
        "consumer",
        required_capabilities=[
            {
                "plugin_id": "provider",
                "capability_id": "sample-api",
                "version_spec": ">=1,<2",
            }
        ],
    )
    unavailable = discovered(
        "unavailable",
        python_requires=["definitely-missing-icarus-package>=1"],
    )
    dependent = discovered(
        "dependent",
        required_capabilities=[
            {
                "plugin_id": "unavailable",
                "capability_id": "missing",
                "version_spec": ">=1",
            }
        ],
    )

    result = resolve_plugins(
        (consumer, unavailable, dependent, provider),
        required_plugin_ids=frozenset({"provider", "consumer"}),
    )

    assert [item.manifest.plugin_id for item in result.plugins] == [
        "provider",
        "consumer",
    ]
    assert result.disabled_plugin_ids == {"unavailable", "dependent"}


def test_resolver核心plugin依赖缺失时启动失败():
    consumer = discovered(
        "consumer",
        required_capabilities=[
            {
                "plugin_id": "missing",
                "capability_id": "sample-api",
                "version_spec": ">=1",
            }
        ],
    )

    with pytest.raises(RequiredPluginError, match="consumer"):
        resolve_plugins(
            (consumer,),
            required_plugin_ids=frozenset({"consumer"}),
        )


def test_resolver重复tool禁用所有可选提供者():
    result = resolve_plugins(
        (
            discovered("first", provided_tools=["same-tool"]),
            discovered("second", provided_tools=["same-tool"]),
        ),
        required_plugin_ids=frozenset(),
        diagnostics=RuntimeDiagnostics(),
    )

    assert result.plugins == ()
    assert result.disabled_plugin_ids == {"first", "second"}


def test_resolver能力循环涉及核心plugin时失败():
    first = discovered(
        "first",
        provided_capabilities=[
            {"capability_id": "first-api", "version": "1.0.0"}
        ],
        required_capabilities=[
            {
                "plugin_id": "second",
                "capability_id": "second-api",
                "version_spec": ">=1",
            }
        ],
    )
    second = discovered(
        "second",
        provided_capabilities=[
            {"capability_id": "second-api", "version": "1.0.0"}
        ],
        required_capabilities=[
            {
                "plugin_id": "first",
                "capability_id": "first-api",
                "version_spec": ">=1",
            }
        ],
    )

    with pytest.raises(RequiredPluginError, match="cycle"):
        resolve_plugins(
            (first, second),
            required_plugin_ids=frozenset({"first"}),
        )


def test_resolver核心plugin之间tool重名时失败():
    with pytest.raises(RequiredPluginError, match="duplicate Tool"):
        resolve_plugins(
            (
                discovered("first", provided_tools=["same-tool"]),
                discovered("second", provided_tools=["same-tool"]),
            ),
            required_plugin_ids=frozenset({"first", "second"}),
        )
