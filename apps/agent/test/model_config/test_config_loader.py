import json

import pydantic
import pytest

import apps.agent as agent_package
from apps.agent.src.model_config import config_loader
from apps.agent.src.model_config import ConfigModel, get_config


def model_settings():
    return {
        role: {
            "model_name": f"{role}-model",
            "context_window": 128000,
            "max_tokens": 4096,
            "temperature": 0.2,
            "default_think_level": "medium",
        }
        for role in ("thinking", "perception")
    }


def test_get_config_reads_models_secrets_and_skill_permissions(monkeypatch, tmp_path):
    settings = {
        "openai_base_url": "https://openai.example.com/v1",
        "anthropic_base_url": "https://anthropic.example.com",
        "skill": {"allow_produce": True, "allow_evolve": False},
        "model_settings": model_settings(),
        "use_protocol": "anthropic",
    }
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(json.dumps(settings), encoding="utf-8")
    monkeypatch.setattr(config_loader, "_settings_resource", lambda: settings_path)
    monkeypatch.setenv("OPENAI_API_KEY", "openai-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-key")

    config = get_config()

    assert config.openai_api_key == "openai-key"
    assert config.anthropic_api_key == "anthropic-key"
    assert config.use_protocol == "anthropic"
    assert config.skill.allow_produce is True
    assert config.skill.allow_evolve is False
    assert config.model_settings.thinking.model_name == "thinking-model"


def test_get_config_default_resource_is_packaged():
    assert agent_package.__file__ is not None
    resource = config_loader._settings_resource()
    assert resource.name == "settings.json"
    assert resource.is_file()
    with resource.open("r", encoding="utf-8") as file:
        assert json.load(file)["model_settings"]["thinking"]["model_name"]


def test_config_model_skill_permissions_default_disabled():
    config = ConfigModel(
        openai_base_url="https://openai.example.com/v1",
        anthropic_base_url="https://anthropic.example.com",
        model_settings=model_settings(),
    )
    assert config.skill.allow_produce is False
    assert config.skill.allow_evolve is False
    assert config.agent.max_steps == 256


def test_config_model拒绝非法agent_step和context_window():
    settings = model_settings()
    settings["thinking"]["context_window"] = 0
    with pytest.raises(pydantic.ValidationError, match="context_window"):
        ConfigModel(
            openai_base_url="https://openai.example.com/v1",
            anthropic_base_url="https://anthropic.example.com",
            model_settings=settings,
        )
    with pytest.raises(pydantic.ValidationError, match="max_steps"):
        ConfigModel(
            openai_base_url="https://openai.example.com/v1",
            anthropic_base_url="https://anthropic.example.com",
            agent={"max_steps": 0},
            model_settings=model_settings(),
        )


@pytest.mark.parametrize("field", ["allow_produce", "allow_evolve"])
@pytest.mark.parametrize("value", [0, 1, "true", None])
def test_config_model_skill_permissions_are_strict_booleans(field, value):
    with pytest.raises(pydantic.ValidationError, match=field):
        ConfigModel(
            openai_base_url="https://openai.example.com/v1",
            anthropic_base_url="https://anthropic.example.com",
            skill={field: value},
            model_settings=model_settings(),
        )
