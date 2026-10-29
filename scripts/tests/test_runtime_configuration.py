"""Runtime metadata is TOML-owned; dotenv contains credentials, not topology."""

from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'icarus'))
from configuration import load_configuration
from main import IcarusControl
from registry import RegistryError


BASE = '''version = 1
[runtime]
data_dir = "~/.icarus"
env = { NO_PROXY = "localhost,127.0.0.1", SNOWLUMA_MCP_ENDPOINT = "http://127.0.0.1:3010/" }
[components.service]
kind = "app"
provisioning = "process"
'''


def test_toml_runtime_defaults_and_local_overlay_feed_children(tmp_path, monkeypatch):
    (tmp_path / 'icarus.toml').write_text(BASE)
    (tmp_path / 'icarus.local.toml').write_text('[runtime]\ndata_dir = "' + str(tmp_path / 'data') + '"\n')
    registry = load_configuration(tmp_path)
    runtime = IcarusControl(tmp_path, environ={})._runtime(registry)
    assert runtime.environment['ICARUS_DATA_DIR'] == str(tmp_path / 'data')
    assert runtime.environment['NO_PROXY'] == 'localhost,127.0.0.1'
    assert runtime.environment['SNOWLUMA_MCP_ENDPOINT'] == 'http://127.0.0.1:3010/'


def test_runtime_respects_explicit_dotenv_isolation(tmp_path):
    (tmp_path / 'icarus.toml').write_text(BASE)
    (tmp_path / '.env').write_text('ONLY_REPOSITORY=excluded\n')
    alternate = tmp_path / 'alternate.env'
    alternate.write_text('ONLY_ALTERNATE=selected\n')
    registry = load_configuration(tmp_path)
    runtime = IcarusControl(tmp_path, environ={'ICARUS_ENV_FILE': str(alternate)})._runtime(registry)
    assert runtime.environment['ONLY_ALTERNATE'] == 'selected'
    assert 'ONLY_REPOSITORY' not in runtime.environment


def test_explicit_process_data_path_takes_precedence(tmp_path):
    (tmp_path / 'icarus.toml').write_text(BASE)
    registry = load_configuration(tmp_path)
    runtime = IcarusControl(tmp_path, environ={'ICARUS_DATA_DIR': str(tmp_path / 'explicit')})._runtime(registry)
    assert runtime.environment['ICARUS_DATA_DIR'] == str(tmp_path / 'explicit')


@pytest.mark.parametrize('entry', ['data_dir = "relative"', 'env = { API_KEY = "must-not-leak" }'])
def test_invalid_runtime_metadata_is_rejected_without_secret_echo(tmp_path, entry):
    (tmp_path / 'icarus.toml').write_text(BASE.replace('data_dir = "~/.icarus"\nenv = { NO_PROXY = "localhost,127.0.0.1", SNOWLUMA_MCP_ENDPOINT = "http://127.0.0.1:3010/" }', entry))
    with pytest.raises(RegistryError) as error:
        load_configuration(tmp_path)
    assert 'must-not-leak' not in str(error.value)
