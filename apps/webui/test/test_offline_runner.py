"""The offline runner owns server cleanup and cannot inherit live-test opt-ins."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


def runner_module():
    path = Path(__file__).resolve().parents[1] / "scripts/test.py"
    spec = importlib.util.spec_from_file_location("webui_offline_runner", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("result", [0, 7])
def test_runner_preserves_exit_code_cleans_server_and_removes_live_optins(monkeypatch, tmp_path, result):
    runner = runner_module()
    monkeypatch.setattr(runner, "APP", tmp_path)
    monkeypatch.setattr(runner.shutil, "which", lambda _: "pnpm")
    monkeypatch.setenv("WEBUI_LIVE_MODEL", "1")
    monkeypatch.setenv("WEBUI_LIVE_TESTS", "1")
    monkeypatch.setenv("ICARUS_MEM0_ENDPOINT", "https://developer-service.invalid")
    monkeypatch.setenv("ICARUS_MEM0_API_KEY", "developer-key")
    monkeypatch.setattr("sys.argv", ["test.py", "--junitxml=result.xml"])
    calls = []
    process = SimpleNamespace(pid=123456, poll=lambda: None, wait=lambda **kwargs: calls.append("wait"))
    monkeypatch.setattr(runner.subprocess, "Popen", lambda *args, **kwargs: process)
    def call(command, **kwargs):
        assert command[-1] == "--junitxml=result.xml"
        assert not any(key.startswith("WEBUI_LIVE_") for key in kwargs["env"])
        assert kwargs["env"]["WEBUI_BASE_URL"].startswith("http://127.0.0.1:")
        for service in ("MEM0", "OPENKB", "GATEWAY", "RELATIONSHIP"):
            assert kwargs["env"][f"ICARUS_{service}_ENDPOINT"] == "http://127.0.0.1:9"
        assert kwargs["env"]["ICARUS_MEM0_API_KEY"] == "offline-test-only"
        return result
    monkeypatch.setattr(runner.subprocess, "call", call)
    monkeypatch.setattr(runner.subprocess, "run", lambda *args, **kwargs: calls.append("cleanup"))
    if hasattr(runner.os, "killpg"):
        monkeypatch.setattr(runner.os, "killpg", lambda *args: calls.append("cleanup"))
    class Response:
        status = 200
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
    monkeypatch.setattr(runner, "urlopen", lambda *args, **kwargs: Response())
    assert runner.main() == result
    assert calls == ["cleanup", "wait"]


def test_runner_cleans_server_when_startup_fails(monkeypatch, tmp_path):
    runner = runner_module()
    monkeypatch.setattr(runner, "APP", tmp_path)
    monkeypatch.setattr(runner.shutil, "which", lambda _: "pnpm")
    calls = []
    process = SimpleNamespace(pid=123456, poll=lambda: 1, wait=lambda **kwargs: calls.append("wait"))
    monkeypatch.setattr(runner.subprocess, "Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr(runner.subprocess, "run", lambda *args, **kwargs: calls.append("cleanup"))
    if hasattr(runner.os, "killpg"):
        monkeypatch.setattr(runner.os, "killpg", lambda *args: calls.append("cleanup"))
    with pytest.raises(RuntimeError, match="exited"):
        runner.main()
    assert calls == ["cleanup", "wait"]
