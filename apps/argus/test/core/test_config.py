import json
from pathlib import Path

import pytest
from pydantic import ValidationError


def test_config_uses_explicit_fixture_not_real_settings(tmp_path, monkeypatch):
    from apps.argus.src.core import config
    path = tmp_path / 'settings.json'
    path.write_text(json.dumps({'port': 9876, 'qq': {'enabled': False}}))
    monkeypatch.setattr(config, 'load_icarus_environment', lambda: None)
    monkeypatch.setenv('ARGUS_DATABASE_URL', 'postgresql+asyncpg://test:test@127.0.0.1/argus_test')
    result = config.load_config(settings_path=path)
    assert result.port == 9876
    assert result.qq.enabled is False
    assert 'test:test' not in repr(result)


@pytest.mark.parametrize('values', [
    {'host': '0.0.0.0'}, {'max_items_per_wake': 0},
    {'quiet_seconds': float('nan')}, {'default_permission_level': 6},
    {'max_submit_attempts': 2, 'retry_delays_seconds': [1, 2]},
    {'service_host': '0.0.0.0', 'service_port': 9000},
    {'service_host': '8.8.8.8', 'service_port': 9000},
    {'service_host': '100.70.8.2'},
])
def test_invalid_config_is_rejected(values):
    from apps.argus.src.core.config import ArgusConfig
    with pytest.raises(ValidationError):
        ArgusConfig(**values)


def test_sqlite_is_rejected():
    from apps.argus.src.core.database import Database
    with pytest.raises(ValueError, match='PostgreSQL'):
        Database('sqlite+aiosqlite:///:memory:')


def test_qq_and_jev_enabled_require_separate_secrets(tmp_path, monkeypatch):
    from apps.argus.src.core import config
    path = tmp_path / 'settings.json'
    path.write_text(json.dumps({'qq': {'enabled': True, 'bot_id': '123'}, 'jev': {'enabled': True}}))
    monkeypatch.setenv('ARGUS_DATABASE_URL', 'postgresql+asyncpg://test:test@127.0.0.1/argus_test')
    with pytest.raises(ValueError, match='ARGUS_QQ_REPORT_SECRET'):
        config.load_config(settings_path=path)
    monkeypatch.setenv('ARGUS_QQ_REPORT_SECRET', 'synthetic')
    with pytest.raises(ValueError, match='ARGUS_JEV_API_KEY'):
        config.load_config(settings_path=path)


def test_config_errors_hide_accidentally_supplied_secret():
    from apps.argus.src.core.config import ArgusConfig
    with pytest.raises(ValidationError) as error:
        ArgusConfig(jev={'api_key': 'synthetic-secret-must-not-leak'})
    assert 'synthetic-secret-must-not-leak' not in str(error.value)


def test_qq_history_enabled_requires_its_token(tmp_path, monkeypatch):
    from apps.argus.src.core import config
    path = tmp_path / 'settings.json'
    path.write_text(json.dumps({'qq': {'enabled': True, 'bot_id': '123',
        'history': {'enabled': True, 'endpoint': 'http://127.0.0.1:3010/'}}}))
    monkeypatch.setenv('ARGUS_DATABASE_URL', 'postgresql+asyncpg://test:test@127.0.0.1/argus_test')
    monkeypatch.setenv('ARGUS_QQ_REPORT_SECRET', 'synthetic')
    monkeypatch.delenv('SNOWLUMA_MCP_TOKEN', raising=False)
    with pytest.raises(ValueError, match='SNOWLUMA_MCP_TOKEN'):
        config.load_config(settings_path=path)


def test_database_url_can_be_built_from_injected_topology_and_secret(tmp_path, monkeypatch):
    from apps.argus.src.core import config
    from sqlalchemy.engine import make_url
    path = tmp_path / 'settings.json'
    path.write_text('{}')
    monkeypatch.setattr(config, 'load_icarus_environment', lambda: None)
    monkeypatch.delenv('ARGUS_DATABASE_URL', raising=False)
    for key, value in {'ARGUS_POSTGRES_HOST': '::1', 'ARGUS_POSTGRES_PORT': '15432',
                       'ARGUS_POSTGRES_DB': 'argus', 'ARGUS_POSTGRES_USER': 'argus',
                       'ARGUS_POSTGRES_PASSWORD': 'p@ss:/?#word'}.items():
        monkeypatch.setenv(key, value)
    result = config.load_config(settings_path=path)
    url = make_url(result.database_url.get_secret_value())
    assert (url.host, url.port, url.database, url.username) == ('::1', 15432, 'argus', 'argus')
    assert url.password == 'p@ss:/?#word'
    assert 'p@ss' not in repr(result)


def test_missing_database_url_is_explicit(tmp_path, monkeypatch):
    from apps.argus.src.core import config
    path = tmp_path / 'settings.json'
    path.write_text('{}')
    monkeypatch.delenv('ARGUS_DATABASE_URL', raising=False)
    monkeypatch.delenv('ARGUS_POSTGRES_HOST', raising=False)
    monkeypatch.delenv('ARGUS_POSTGRES_PASSWORD', raising=False)
    monkeypatch.setattr(config, 'load_icarus_environment', lambda: None)
    with pytest.raises(ValueError, match='ARGUS_DATABASE_URL'):
        config.load_config(settings_path=path)
