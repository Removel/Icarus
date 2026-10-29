"""Runtime metadata is TOML-owned; dotenv carries credentials."""

from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "icarus"))
from configuration import configuration_document, load_configuration
from main import IcarusControl
from registry import RegistryError


BASE = '''version = 1
[runtime]
data_dir = "~/.icarus"
env = { NO_PROXY = "localhost,127.0.0.1", FEATURE_ENABLED = true }
[components.service]
kind = "app"
provisioning = "process"
'''


def test_toml_runtime_defaults_and_local_overlay_feed_children(tmp_path):
    (tmp_path / "icarus.toml").write_text(BASE)
    (tmp_path / "icarus.local.toml").write_text(
        '[runtime]\ndata_dir = "' + str(tmp_path / "data") + '"\n'
        '[runtime.env]\nNO_PROXY = "localhost,127.0.0.1,service.internal"\n'
    )
    registry = load_configuration(tmp_path)
    runtime = IcarusControl(tmp_path, environ={})._runtime(registry)
    assert runtime.environment["ICARUS_DATA_DIR"] == str(tmp_path / "data")
    assert runtime.environment["NO_PROXY"] == "localhost,127.0.0.1,service.internal"
    assert runtime.environment["FEATURE_ENABLED"] == "true"
    assert runtime.environment["ICARUS_ENVIRONMENT_READY"] == "1"
    document = configuration_document(registry, tmp_path)
    assert document["runtime"]["data_dir"] == str(tmp_path / "data")
    assert document["runtime"]["env"]["FEATURE_ENABLED"] is True


def test_runtime_respects_explicit_dotenv_isolation(tmp_path):
    (tmp_path / "icarus.toml").write_text(BASE)
    (tmp_path / ".env").write_text("ONLY_REPOSITORY=excluded\n")
    alternate = tmp_path / "alternate.env"
    alternate.write_text("ONLY_ALTERNATE=selected\n")
    registry = load_configuration(tmp_path)
    runtime = IcarusControl(tmp_path, environ={"ICARUS_ENV_FILE": str(alternate)})._runtime(registry)
    assert runtime.environment["ONLY_ALTERNATE"] == "selected"
    assert "ONLY_REPOSITORY" not in runtime.environment


def test_explicit_process_values_override_runtime_and_dotenv(tmp_path):
    (tmp_path / "icarus.toml").write_text(BASE)
    (tmp_path / ".env").write_text("NO_PROXY=dotenv\nFEATURE_ENABLED=dotenv\n")
    registry = load_configuration(tmp_path)
    runtime = IcarusControl(tmp_path, environ={
        "ICARUS_DATA_DIR": str(tmp_path / "explicit"), "NO_PROXY": "process",
    })._runtime(registry)
    assert runtime.environment["ICARUS_DATA_DIR"] == str(tmp_path / "explicit")
    assert runtime.environment["NO_PROXY"] == "process"
    assert runtime.environment["FEATURE_ENABLED"] == "true"


@pytest.mark.parametrize("entry", [
    'data_dir = "relative"', 'env = { API_KEY = "must-not-leak" }',
    'env = { NO_PROXY = ["localhost"] }',
])
def test_invalid_runtime_metadata_is_rejected_without_secret_echo(tmp_path, entry):
    (tmp_path / "icarus.toml").write_text(
        'version = 1\n[runtime]\n' + entry + '\n[components.service]\n'
        'kind = "app"\nprovisioning = "process"\n'
    )
    with pytest.raises(RegistryError) as error:
        load_configuration(tmp_path)
    assert "must-not-leak" not in str(error.value)
