from importlib import import_module
from pathlib import Path

import pytest


def test_configuration_builds_private_argus_dsn_and_keeps_secrets_hidden(monkeypatch, tmp_path):
    try:
        Config = import_module("argus_app.config").ArgusConfig
    except ModuleNotFoundError:
        Config = None
    assert Config is not None, "Argus must have an app-owned configuration loader"
    monkeypatch.setenv("ARGUS_PG_PASSWORD", "test:/secret")
    monkeypatch.setenv("ARGUS_AGENT_API_TOKEN", "callback-test-secret")
    monkeypatch.setenv("ARGUS_GATEWAY_TOKEN", "gateway-test-secret")
    monkeypatch.setenv("ARGUS_DATA_DIR", str(tmp_path))
    config = Config.from_environment()
    assert config.database_url.get_secret_value().endswith("@127.0.0.1:15432/argus")
    assert "test%3A%2Fsecret" in config.database_url.get_secret_value()
    assert "callback-test-secret" not in repr(config)
    assert config.data_dir == tmp_path


def test_configuration_does_not_connect_or_create_database(monkeypatch, tmp_path):
    from argus_app.config import ArgusConfig
    monkeypatch.delenv("ARGUS_DATABASE_URL", raising=False)
    monkeypatch.delenv("ARGUS_PG_PASSWORD", raising=False)
    monkeypatch.setenv("ARGUS_DATA_DIR", str(tmp_path))
    with pytest.raises(ValueError, match="ARGUS_PG_PASSWORD"):
        ArgusConfig.from_environment()


def test_discovery_fallback_is_explicit_and_requires_named_model(tmp_path):
    from argus_app.config import ArgusConfig
    from pydantic import SecretStr
    values = dict(database_url=SecretStr('postgresql+asyncpg://unused/argus_test'), data_dir=tmp_path,
                  agent_token=SecretStr('agent-test'), gateway_token=SecretStr('gateway-test'))
    config = ArgusConfig(**values)
    assert config.discovery_fallback_enabled is False
    with pytest.raises(ValueError, match='discovery_fallback_model'):
        ArgusConfig(**values, discovery_fallback_enabled=True)
    with pytest.raises(ValueError, match='discovery_fallback_model'):
        ArgusConfig(**values, discovery_fallback_enabled=True, discovery_fallback_model='  ')


def test_discovery_fallback_environment_uses_existing_settings(monkeypatch, tmp_path):
    from argus_app.config import ArgusConfig
    monkeypatch.setenv('ARGUS_DATABASE_URL', 'postgresql+asyncpg://unused/argus_test')
    monkeypatch.setenv('ARGUS_AGENT_API_TOKEN', 'agent-test')
    monkeypatch.setenv('ARGUS_GATEWAY_TOKEN', 'gateway-test')
    monkeypatch.setenv('ARGUS_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('ARGUS_DISCOVERY_FALLBACK_ENABLED', 'true')
    monkeypatch.setenv('ARGUS_DISCOVERY_FALLBACK_MODEL', 'perception')
    monkeypatch.setenv('ARGUS_DISCOVERY_FALLBACK_SETTINGS_PATH', str(tmp_path / 'models.json'))
    config = ArgusConfig.from_environment()
    assert config.discovery_fallback_enabled is True
    assert config.discovery_fallback_model == 'perception'
    assert config.discovery_fallback_settings_path == tmp_path / 'models.json'


def test_channel_naming_defaults_to_perception_and_can_be_disabled(monkeypatch,tmp_path):
    from argus_app.config import ArgusConfig
    monkeypatch.setenv('ARGUS_DATABASE_URL','postgresql+asyncpg://unused/argus_test')
    monkeypatch.setenv('ARGUS_AGENT_API_TOKEN','synthetic')
    monkeypatch.setenv('ARGUS_GATEWAY_TOKEN','synthetic')
    monkeypatch.setenv('ARGUS_DATA_DIR',str(tmp_path))
    monkeypatch.delenv('ARGUS_CHANNEL_NAMING_ENABLED',raising=False)
    monkeypatch.delenv('ARGUS_CHANNEL_NAMING_MODEL',raising=False)
    config=ArgusConfig.from_environment()
    assert config.channel_naming_enabled and config.channel_naming_model=='perception'
    monkeypatch.setenv('ARGUS_CHANNEL_NAMING_ENABLED','false')
    monkeypatch.setenv('ARGUS_CHANNEL_NAMING_MODEL','custom-title-model')
    config=ArgusConfig.from_environment()
    assert not config.channel_naming_enabled and config.channel_naming_model=='custom-title-model'
