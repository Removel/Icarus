import json

import pydantic
import pytest

import apps.agent as agent_package
from agent_app.config import config_loader
from agent_app.config import ConfigModel, get_config


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
    assert config.get_model("thinking").model_name == "thinking-model"


def test_get_config_default_resource_is_packaged():
    assert agent_package.__file__ is not None
    resource = config_loader._settings_resource()
    assert resource.name == "settings.json"
    assert resource.is_file()
    with resource.open("r", encoding="utf-8") as file:
        settings = json.load(file)
    assert next(item for item in settings["model_settings"] if item["name"] == "thinking")["model_name"]
    assert settings["agent"]["tool_execution"] == {
        "default_timeout_seconds": 120.0,
        "default_output_tokens": 3000,
    }
    config = get_config()
    assert config.agent.tool_execution.max_timeout_seconds == 600
    assert config.agent.tool_execution.batch_output_tokens == 16000
    assert config.agent.tool_execution.max_result_file_bytes == 16 * 1024 * 1024


def test_config_model_skill_permissions_default_disabled():
    config = ConfigModel(
        openai_base_url="https://openai.example.com/v1",
        anthropic_base_url="https://anthropic.example.com",
        model_settings=model_settings(),
    )
    assert config.skill.allow_produce is False
    assert config.skill.allow_evolve is False
    assert config.agent.max_steps == 256
    assert config.mcp_servers == {}
    assert "mcp" in config.runtime.required_plugin_ids
    assert "memory" in config.runtime.required_plugin_ids
    assert "knowledge" in config.runtime.required_plugin_ids
    assert "process" in config.runtime.required_plugin_ids


def test_config_model读取通用mcp_servers并保留server字段():
    config = ConfigModel(
        openai_base_url="https://openai.example.com/v1",
        anthropic_base_url="https://anthropic.example.com",
        mcpServers={
            "local": {
                "command": "npx",
                "args": ["-y", "example-mcp"],
                "customField": "preserved",
            },
            "remote": {
                "url": "https://example.com/mcp",
                "headers": {"Authorization": "Bearer ${MCP_TOKEN}"},
            },
        },
        model_settings=model_settings(),
    )

    assert config.mcp_servers["local"]["command"] == "npx"
    assert config.mcp_servers["local"]["customField"] == "preserved"
    assert config.mcp_servers["remote"]["headers"] == {
        "Authorization": "Bearer ${MCP_TOKEN}"
    }


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


def endpoint_settings():
    return {
        "openai_base_url": "https://model.example.invalid/v1",
        "anthropic_base_url": "https://model.example.invalid",
        "model_settings": [{"name": "m", "model_name": "m", "context_window": 1000,
                            "max_tokens": 100, "temperature": 0, "default_think_level": "low"}],
        "model_roles": {"agent": "m", "compaction": "m", "skill_generation": "m", "title": "m"},
        "agent": {"max_steps": 17},
        "skill": {"allow_produce": True},
    }


@pytest.fixture
def endpoint_environment(monkeypatch):
    for name in ("ICARUS_MEM0_ENDPOINT", "ICARUS_OPENKB_ENDPOINT", "ICARUS_RELATIONSHIP_ENDPOINT"):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def test_endpoint_environment_overrides_only_plugin_connections(tmp_path, endpoint_environment):
    settings = endpoint_settings() | {"runtime": {
        "required_plugin_ids": ["agent"],
        "plugin_config": {
            "memory": {"endpoint": "http://127.0.0.1:8888", "user_id": "u", "agent_id": "a"},
            "knowledge": {"endpoint": "http://127.0.0.1:7566", "knowledge_base": "notes"},
            "relationship": {"endpoint": "http://127.0.0.1:8767", "timeout_seconds": 23},
            "other": {"keep": True},
        },
    }}
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(settings), encoding="utf-8")
    endpoint_environment.setenv("ICARUS_MEM0_ENDPOINT", "https://mem0.example.invalid")
    endpoint_environment.setenv("ICARUS_OPENKB_ENDPOINT", "https://openkb.example.invalid")
    endpoint_environment.setenv("ICARUS_RELATIONSHIP_ENDPOINT", "https://relationship.example.invalid")

    config = get_config(path)

    assert config.runtime.plugin_config == {
        "memory": {"endpoint": "https://mem0.example.invalid", "user_id": "u", "agent_id": "a"},
        "knowledge": {"endpoint": "https://openkb.example.invalid", "knowledge_base": "notes"},
        "relationship": {"endpoint": "https://relationship.example.invalid", "timeout_seconds": 23},
        "other": {"keep": True},
    }
    assert config.runtime.required_plugin_ids == ["agent"]
    assert config.model_roles.model_dump() == {
        "agent": "m", "compaction": "m", "skill_generation": "m", "title": "m",
    }
    assert config.model_settings[0].model_name == "m"
    assert config.agent.max_steps == 17
    assert config.skill.allow_produce is True
    assert json.loads(path.read_text(encoding="utf-8")) == settings


@pytest.mark.parametrize("runtime", [None, {}, {"plugin_config": {}}])
def test_endpoint_environment_creates_only_requested_plugin_slot(tmp_path, endpoint_environment, runtime):
    settings = endpoint_settings()
    if runtime is not None:
        settings["runtime"] = runtime
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(settings), encoding="utf-8")
    endpoint_environment.setenv("ICARUS_RELATIONSHIP_ENDPOINT", "https://relationship.example.invalid")

    config = get_config(path)

    assert config.runtime.plugin_config == {
        "relationship": {"endpoint": "https://relationship.example.invalid"},
    }
    assert "relationship" not in config.runtime.required_plugin_ids


@pytest.mark.parametrize("runtime, expected", [
    (None, {}),
    ({"plugin_config": {"memory": {"endpoint": "http://127.0.0.1:9999", "user_id": "u"}}},
     {"memory": {"endpoint": "http://127.0.0.1:9999", "user_id": "u"}}),
])
def test_endpoint_environment_unset_preserves_file(tmp_path, endpoint_environment, runtime, expected):
    settings = endpoint_settings()
    if runtime is not None:
        settings["runtime"] = runtime
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(settings), encoding="utf-8")

    assert get_config(path).runtime.plugin_config == expected


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
