import json

import pytest
from pydantic import ValidationError

from apps.agent.src.agent_orchestration.plugin_runtime.manifest import (
    PluginManifest,
    parse_manifest_text,
)


def manifest_data(**changes):
    data = {
        "schema_version": 1,
        "plugin_id": "sample",
        "plugin_version": "1.0.0",
        "entrypoint": "sample.factory:create_plugin",
        "python_requires": [],
        "required_capabilities": [],
        "provided_capabilities": [],
        "provided_tools": [],
        "published_events": [],
        "consumed_events": [],
        "state_scopes": [],
    }
    data.update(changes)
    return data


def test_manifest解析完整扁平结构并生成稳定hash():
    text = json.dumps(manifest_data(), sort_keys=True)

    manifest, manifest_hash = parse_manifest_text(text)

    assert manifest.plugin_id == "sample"
    assert len(manifest_hash) == 64
    assert parse_manifest_text(text)[1] == manifest_hash


@pytest.mark.parametrize(
    "changes, error",
    [
        ({"schema_version": 2}, "schema_version"),
        ({"plugin_version": "latest"}, "valid version"),
        ({"entrypoint": "sample.factory"}, "entrypoint"),
        ({"provided_tools": ["same", "same"]}, "duplicates"),
        ({"state_scopes": ["workspace"]}, "declared together"),
    ],
)
def test_manifest拒绝不明确或不一致声明(changes, error):
    with pytest.raises(ValidationError, match=error):
        PluginManifest.model_validate(manifest_data(**changes))


def test_manifest要求状态范围和版本成对出现():
    manifest = PluginManifest.model_validate(
        manifest_data(
            required_capabilities=[
                {
                    "plugin_id": "persistence",
                    "capability_id": "state_store",
                    "version_spec": ">=1,<2",
                }
            ],
            state_scopes=["workspace", "session"],
            workspace_state_version=1,
            session_state_version=2,
        )
    )

    assert manifest.workspace_state_version == 1
    assert manifest.session_state_version == 2


def test_manifest声明状态时必须依赖统一state_store():
    with pytest.raises(ValidationError, match="persistence/state_store"):
        PluginManifest.model_validate(
            manifest_data(
                state_scopes=["session"],
                session_state_version=1,
            )
        )
