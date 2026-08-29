from pathlib import Path

import pytest

from packages import runtime_environment


def test_get_icarus_data_dir优先使用已有环境变量(monkeypatch, tmp_path):
    monkeypatch.setenv("ICARUS_DATA_DIR", str(tmp_path))

    assert runtime_environment.get_icarus_data_dir() == tmp_path.resolve()


def test_get_icarus_data_dir拒绝相对路径(monkeypatch):
    monkeypatch.setenv("ICARUS_DATA_DIR", "relative")

    with pytest.raises(RuntimeError, match="absolute"):
        runtime_environment.get_icarus_data_dir()
