"""Launcher diagnostics, without starting application services."""
import os
from pathlib import Path
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[2]
CLI = ROOT / "bin/icarus"
pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX launcher")


def test_unavailable_working_directory_is_not_reported_as_python_version(tmp_path):
    workdir = tmp_path / "vanished"
    workdir.mkdir()
    result = subprocess.run(
        ["bash", "-c", 'rmdir -- "$PWD"; exec "$1" --help', "launcher-test", str(CLI)],
        cwd=workdir, capture_output=True, text=True, timeout=10,
    )
    assert result.returncode != 0
    assert "current working directory" in result.stderr
    assert "cd /" in result.stderr
    assert "requires Python" not in result.stderr


@pytest.mark.parametrize("exit_code,expected", [
    (42, "requires Python 3.11 or newer"),
    (1, "Python failed to start"),
])
def test_python_version_and_interpreter_failure_have_distinct_diagnostics(tmp_path, exit_code, expected):
    interpreter = tmp_path / "test-python"
    interpreter.write_text(f"#!/bin/sh\nexit {exit_code}\n")
    interpreter.chmod(0o755)
    result = subprocess.run(
        [str(CLI), "--help"], cwd=tmp_path,
        env={**os.environ, "PYTHON": str(interpreter)},
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode != 0
    assert expected in result.stderr
    if exit_code != 42:
        assert "requires Python" not in result.stderr
