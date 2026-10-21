"""Manifest contracts are provider-independent and discovered without imports."""

import pytest
from pydantic import ValidationError

from icarus.core.manifest import PluginManifest, RequiredCapabilityManifest


def test_requirement_uses_contract_not_provider_identity():
    requirement = RequiredCapabilityManifest(capability_id="conversation.read", version_spec=">=1,<2")
    assert requirement.model_dump() == {"capability_id": "conversation.read", "version_spec": ">=1,<2"}


def test_manifest_declares_tier_config_and_stable_event_ids():
    manifest = PluginManifest.model_validate({
        "schema_version": 2, "plugin_id": "sample", "plugin_version": "1.0.0",
        "entrypoint": "sample.factory:create_plugin", "tier": "core", "required_config": ["endpoint"],
        "python_requires": [], "required_capabilities": [], "provided_capabilities": [],
        "provided_tools": [], "published_events": ["input.user_input"],
        "consumed_events": [], "state_scopes": [],
    })
    assert manifest.tier == "core"
    assert manifest.required_config == ("endpoint",)
    assert manifest.published_events == ("input.user_input",)


def test_manifest_rejects_old_schema():
    with pytest.raises(ValidationError):
        PluginManifest.model_validate({"schema_version": 1})
