import asyncio
import json
from pathlib import Path
import subprocess
import sys

import pytest

from apps.tui.src import main as main_module


def test_run_app_使用启动目录透传session_id并返回textual结果(monkeypatch, tmp_path):
    captured = {}

    service = object()

    async def create_runtime_service(workspace_path, session_id):
        assert captured["app_run_entered"] is True
        captured["service_workspace"] = workspace_path
        captured["session_id"] = session_id
        return service

    class AppStub:
        return_code = None

        def __init__(self, *, runtime_factory, workspace_path):
            captured["runtime_factory"] = runtime_factory
            captured["app_workspace"] = workspace_path
            captured["app_run_entered"] = False

        def run(self):
            captured["app_run_entered"] = True
            captured["app_service"] = asyncio.run(
                captured["runtime_factory"]()
            )
            return 7

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        main_module,
        "_create_runtime_service",
        create_runtime_service,
    )
    monkeypatch.setattr(main_module, "IcarusTextualApp", AppStub)

    result = main_module.run_app(["--session-id", "demo"])

    assert result == 7
    assert captured["service_workspace"] == tmp_path.resolve()
    assert captured["app_workspace"] == tmp_path.resolve()
    assert captured["session_id"] == "demo"
    assert captured["app_service"] is service


def test_parse_args_help不初始化runtime(monkeypatch, capsys):
    async def fail_if_initialized(*args, **kwargs):
        raise AssertionError("runtime should not be initialized")

    monkeypatch.setattr(
        main_module,
        "_create_runtime_service",
        fail_if_initialized,
    )

    with pytest.raises(SystemExit) as exit_info:
        main_module.main(["--help"])

    assert exit_info.value.code == 0
    assert "Icarus Agent terminal client" in capsys.readouterr().out


def test_import_main首帧模块边界不加载agent与provider重依赖():
    project_root = Path(__file__).resolve().parents[3]
    forbidden = [
        "apps.agent.src.application.agent_runtime_service",
        "apps.agent.src.agent_orchestration.agent_factory",
        "openai",
        "anthropic",
        "onnxruntime",
    ]
    code = (
        "import json, sys; "
        "import apps.tui.src.main; "
        f"print(json.dumps([name for name in {forbidden!r} if name in sys.modules]))"
    )

    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=project_root,
        check=True,
        capture_output=True,
        text=True,
    )

    assert json.loads(result.stdout) == []


def test_main_未捕获错误返回1并写入stderr(monkeypatch, capsys):
    def failing_run_app(argv=None):
        raise RuntimeError("broken")

    monkeypatch.setattr(main_module, "run_app", failing_run_app)

    assert main_module.main([]) == 1
    assert "Icarus failed: RuntimeError: broken" in capsys.readouterr().err


def test_main_keyboard_interrupt返回130(monkeypatch):
    def interrupted_run_app(argv=None):
        raise KeyboardInterrupt

    monkeypatch.setattr(main_module, "run_app", interrupted_run_app)

    assert main_module.main([]) == 130


def test_pyproject_console_script指向main入口():
    import tomllib

    project_root = Path(__file__).resolve().parents[3]
    with (project_root / "pyproject.toml").open("rb") as file:
        pyproject = tomllib.load(file)

    assert (
        pyproject["project"]["scripts"]["icarus"]
        == "apps.tui.src.main:main"
    )
    assert "apps/agent/settings.json" not in pyproject["project"]["dependencies"]
    assert pyproject["tool"]["setuptools"]["package-data"]["apps.agent"] == [
        "settings.json"
    ]
    assert pyproject["tool"]["setuptools"]["package-data"]["apps.tui.src"] == [
        "styles.tcss"
    ]
    dependencies = pyproject["project"]["dependencies"]
    assert any(item.startswith("textual>=") for item in dependencies)
    assert not any(item.startswith("prompt-toolkit") for item in dependencies)
    assert pyproject["project"]["optional-dependencies"]["test"] == [
        "pytest-textual-snapshot>=1.1,<2"
    ]
    assert pyproject["tool"]["setuptools"]["packages"]["find"]["exclude"] == [
        "apps.agent.test*",
        "apps.tui.test*",
    ]
