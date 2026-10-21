import pytest
from pydantic import ValidationError

from agent_app.plugins.persona.config import PersonaConfig


@pytest.mark.parametrize("persona_id", ["../outside", ".", "..", "a/b", "A", "", "/tmp/p"])
def test_config_rejects_invalid_persona_id(persona_id):
    with pytest.raises(ValidationError):
        PersonaConfig(persona_id=persona_id)


@pytest.mark.parametrize("settings", [
    {"unknown": True}, {"drift_every_turns": 0}, {"max_dynamic_tokens": 0},
    {"jev_timeout_seconds": float("nan")}, {"relationship_timeout_seconds": float("inf")},
    {"attack_defend_threshold": -0.1}, {"attack_refuse_threshold": 1.1},
    {"attack_defend_threshold": 0.9, "attack_refuse_threshold": 0.5},
    {"jev_base_url": "https://secret:password@example.com/api"},
    {"relationship_endpoint": "http://example.com/?secret=value"},
    {"evolution_max_entries": True}, {"jev_enabled": "false"},
])
def test_config_validates_thresholds_limits_and_endpoints(settings):
    with pytest.raises(ValidationError):
        PersonaConfig(**settings)


def test_defaults_do_not_enable_external_services():
    config = PersonaConfig()
    assert not config.jev_enabled
    assert not config.relationship_enabled
    assert config.persona_id == "default"
