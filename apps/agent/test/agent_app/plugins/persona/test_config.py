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
    {"evolution_enabled": "true"}, {"evolution_max_candidates": 11},
    {"evolution_max_input_tokens": 0}, {"evolution_timeout_seconds": 121},
])
def test_config_validates_thresholds_limits_and_endpoints(settings):
    with pytest.raises(ValidationError):
        PersonaConfig(**settings)


def test_defaults_do_not_enable_external_services():
    config = PersonaConfig()
    assert not config.jev_enabled
    assert not config.relationship_enabled
    assert not config.evolution_enabled
    assert config.persona_id == "default"


def test_legacy_tool_cooldown_is_ignored_with_warning(caplog):
    config = PersonaConfig.model_validate({"evolution_min_turns_between_accepts": 10})
    assert not config.evolution_enabled
    assert "cooldown" in caplog.text
    assert "evolution_min_turns_between_accepts" not in config.model_dump()
