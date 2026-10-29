from pathlib import Path

import pytest

from packages import runtime_environment


def test_direct_entry_uses_toml_runtime_with_process_precedence(monkeypatch, tmp_path):
    (tmp_path / 'packages').mkdir()
    monkeypatch.setattr(runtime_environment, '__file__', str(tmp_path / 'packages/runtime_environment.py'))
    (tmp_path / 'icarus.toml').write_text('version = 1\n[runtime]\ndata_dir = "~/.icarus"\n'
                                            '[runtime.env]\nFEATURE_ENABLED = true\nNO_PROXY = "base"\n')
    (tmp_path / 'icarus.local.toml').write_text('[runtime]\ndata_dir = "' + str(tmp_path / 'data') + '"\n'
                                              '[runtime.env]\nNO_PROXY = "overlay"\n')
    monkeypatch.delenv('ICARUS_DATA_DIR', raising=False)
    monkeypatch.delenv('FEATURE_ENABLED', raising=False)
    monkeypatch.setenv('NO_PROXY', 'process')
    monkeypatch.setattr(runtime_environment, 'load_dotenv', lambda **kwargs: None)
    assert runtime_environment.get_icarus_data_dir() == tmp_path / 'data'
    import os
    assert os.environ['FEATURE_ENABLED'] == 'true'
    assert os.environ['NO_PROXY'] == 'process'


def test_managed_entry_does_not_reload_default_toml_or_dotenv(monkeypatch, tmp_path):
    import os
    (tmp_path / 'packages').mkdir()
    monkeypatch.setattr(runtime_environment, '__file__', str(tmp_path / 'packages/runtime_environment.py'))
    (tmp_path / 'icarus.toml').write_text('invalid TOML [')
    monkeypatch.setenv('ICARUS_ENVIRONMENT_READY', '1')
    monkeypatch.setenv('ICARUS_DATA_DIR', str(tmp_path / 'selected-data'))
    calls = []
    monkeypatch.setattr(runtime_environment, 'load_dotenv', lambda **kwargs: calls.append(kwargs))
    assert runtime_environment.get_icarus_data_dir() == tmp_path / 'selected-data'
    assert os.environ['ICARUS_ENVIRONMENT_READY'] == '1'
    assert calls == []


def test_get_icarus_data_dir优先使用已有环境变量(monkeypatch, tmp_path):
    monkeypatch.setenv("ICARUS_DATA_DIR", str(tmp_path))

    assert runtime_environment.get_icarus_data_dir() == tmp_path.resolve()


def test_get_icarus_data_dir拒绝相对路径(monkeypatch):
    monkeypatch.setenv("ICARUS_DATA_DIR", "relative")

    with pytest.raises(RuntimeError, match="absolute"):
        runtime_environment.get_icarus_data_dir()


def test_load_icarus_environment固定读取仓库根env(monkeypatch):
    calls = []
    monkeypatch.setattr(
        runtime_environment,
        "load_dotenv",
        lambda **kwargs: calls.append(kwargs),
    )

    runtime_environment.load_icarus_environment()

    assert calls == [
        {
            "dotenv_path": Path(runtime_environment.__file__).resolve().parents[1]
            / ".env",
            "override": False,
        }
    ]
