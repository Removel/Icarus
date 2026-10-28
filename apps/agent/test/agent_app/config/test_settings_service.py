import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from agent_app.config.settings_service import EditableSettings, SettingsConflictError, SettingsService


def settings_file(tmp_path):
    source = Path(__file__).parents[3] / 'src/agent_app/resources/default_settings.json'
    data = json.loads(source.read_text(encoding='utf-8'))
    data['openai_api_key'] = 'private-key-not-for-browser'
    path = tmp_path / 'settings.json'
    path.write_text(json.dumps(data), encoding='utf-8')
    return path


def test_shared_settings_save_preserves_other_config_and_redacts_credentials(tmp_path):
    path = settings_file(tmp_path)
    before = json.loads(path.read_text(encoding='utf-8'))
    service = SettingsService(path)
    snapshot = service.get()
    assert 'private-key-not-for-browser' not in json.dumps(snapshot)
    draft = snapshot['settings']
    draft['agent']['max_steps'] = 64
    draft['model_settings'][0]['model_name'] = 'updated-model'
    saved = service.update(snapshot['revision'], EditableSettings.model_validate(draft))
    assert saved['settings']['agent']['max_steps'] == 64
    after = json.loads(path.read_text(encoding='utf-8'))
    assert after['model_settings'][0]['model_name'] == 'updated-model'
    assert after['runtime'] == before['runtime']
    assert after['openai_api_key'] == before['openai_api_key']
    assert saved['revision'] != snapshot['revision']
    with pytest.raises(SettingsConflictError):
        service.update(snapshot['revision'], EditableSettings.model_validate(draft))


def test_invalid_role_does_not_change_settings_file(tmp_path):
    path = settings_file(tmp_path)
    before = path.read_bytes()
    service = SettingsService(path)
    snapshot = service.get()
    draft = snapshot['settings']
    draft['model_roles']['agent'] = 'missing'
    with pytest.raises(ValidationError):
        service.update(snapshot['revision'], EditableSettings.model_validate(draft))
    assert path.read_bytes() == before
