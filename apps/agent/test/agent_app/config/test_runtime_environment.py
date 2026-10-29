from pathlib import Path

import pytest

from packages import runtime_environment


def test_direct_entry_uses_toml_data_directory_without_dotenv_topology(monkeypatch, tmp_path):
    (tmp_path / 'packages').mkdir()
    monkeypatch.setattr(runtime_environment, '__file__', str(tmp_path / 'packages/runtime_environment.py'))
    (tmp_path / 'icarus.toml').write_text('version = 1\n[runtime]\ndata_dir = "~/.icarus"\n')
    (tmp_path / 'icarus.local.toml').write_text('[runtime]\ndata_dir = "' + str(tmp_path / 'data') + '"\n')
    monkeypatch.delenv('ICARUS_DATA_DIR', raising=False)
    monkeypatch.setattr(runtime_environment, 'load_dotenv', lambda **kwargs: None)
    assert runtime_environment.get_icarus_data_dir() == tmp_path / 'data'


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
