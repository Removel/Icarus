"""Keep ordinary pytest runs isolated from local QQ credentials and data."""
import json

import pytest

from apps.argus.src.config import config_loader
from packages import runtime_environment


@pytest.fixture(autouse=True)
def isolated_argus_environment(tmp_path, monkeypatch):
    settings_dir = tmp_path / 'app_config'
    settings_dir.mkdir()
    (settings_dir / 'settings.json').write_text(json.dumps({
        'host': '127.0.0.1', 'port': 8766, 'log_level': 'INFO',
        'queue_capacity': 1000, 'worker_count': 1, 'qq': {'enabled': False},
    }), encoding='utf-8')
    monkeypatch.setenv('ICARUS_DATA_DIR', str(tmp_path / 'runtime_data'))
    monkeypatch.delenv('ARGUS_JEV_API_KEY', raising=False)
    monkeypatch.delenv('ARGUS_QQ_REPORT_SECRET', raising=False)
    monkeypatch.setattr(config_loader, 'files', lambda _: settings_dir)
    monkeypatch.setattr(config_loader, 'load_icarus_environment', lambda: None)
    monkeypatch.setattr(runtime_environment, 'load_icarus_environment', lambda: None)
