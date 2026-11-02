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
