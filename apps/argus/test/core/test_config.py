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


def test_missing_database_url_is_explicit(tmp_path, monkeypatch):
    from apps.argus.src.core import config
    path = tmp_path / 'settings.json'
    path.write_text('{}')
    monkeypatch.delenv('ARGUS_DATABASE_URL', raising=False)
    monkeypatch.setattr(config, 'load_icarus_environment', lambda: None)
    with pytest.raises(ValueError, match='ARGUS_DATABASE_URL'):
        config.load_config(settings_path=path)
