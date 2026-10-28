"""Tests for WSL/host detection and host interop command construction."""

from types import SimpleNamespace
from pathlib import Path

import pytest

from apps.tui.src.platform import host


@pytest.fixture(autouse=True)
def clear_wsl_cache():
    host._wsl_cache = None
    yield
    host._wsl_cache = None


def test_host_is_wsl探测_proc_version(monkeypatch):
    monkeypatch.setattr(
        Path,
        "read_text",
        lambda self, *a, **k: "Linux version 5.15.167.4-microsoft-standard-WSL2",
        raising=False,
    )
    assert host.is_wsl() is True
    # Cached: second call does not re-read.
    monkeypatch.setattr(
        Path,
        "read_text",
        lambda self, *a, **k: "Linux version 6.6 generic",
        raising=False,
    )
    assert host.is_wsl() is True


def test_host_is_wsl非wsl(monkeypatch):
    monkeypatch.setattr(
        Path,
        "read_text",
        lambda self, *a, **k: "Linux version 6.6.87-generic",
        raising=False,
    )
    assert host.is_wsl() is False


def test_host_is_wsl文件不存在(monkeypatch):
    def raise_missing(self, *a, **k):
        raise FileNotFoundError("/proc/version")

    monkeypatch.setattr(Path, "read_text", raise_missing, raising=False)
    assert host.is_wsl() is False


def test_host_powershell_command_argv_and_stdin():
    argv, stdin = host.host_powershell_command("Write-Output x", b"data-payload")
    assert "powershell.exe" in argv[0].lower()
    assert "Write-Output x" in " ".join(argv)
    assert stdin == b"data-payload"
    # The payload travels via stdin, never embedded into the script string.
    assert "data-payload" not in " ".join(argv)
    # Script is a fixed argv element, never routed through a shell.
    assert not any(part.startswith("/c ") or part == "/c" for part in argv)


def test_host_powershell_command_text_encoding():
    argv, stdin = host.host_powershell_command("Read-Host", "中文文本")
    assert stdin == "中文文本".encode("utf-8")
