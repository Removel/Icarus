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

    async def create_gateway_client(
        workspace_path, session_id, gateway_url, create_if_missing
    ):
        assert captured["app_run_entered"] is True
        captured["service_workspace"] = workspace_path
        captured["session_id"] = session_id
        captured["gateway_url"] = gateway_url
        captured["create_if_missing"] = create_if_missing
        return service

    class AppStub:
        return_code = None

        def __init__(
            self,
            *,
            runtime_factory,
            initial_session_id,
            workspace_path,
            resource_root,
        ):
            captured["runtime_factory"] = runtime_factory
            captured["app_workspace"] = workspace_path
            captured["initial_session_id"] = initial_session_id
            captured["resource_root"] = resource_root
            captured["app_run_entered"] = False

        def run(self):
            captured["app_run_entered"] = True
            captured["app_service"] = asyncio.run(
                captured["runtime_factory"]("selected", False)
            )
            return 7

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ICARUS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(
        main_module,
        "_create_gateway_client",
        create_gateway_client,
    )
    monkeypatch.setattr(main_module, "IcarusTextualApp", AppStub)

    result = main_module.run_app(
        ["--session-id", "demo", "--gateway-url", "ws://test/rpc"]
    )

    assert result == 7
    assert captured["service_workspace"] == tmp_path.resolve()
    assert captured["app_workspace"] == tmp_path.resolve()
    assert captured["initial_session_id"] == "demo"
    assert captured["session_id"] == "selected"
    assert captured["create_if_missing"] is False
    assert captured["gateway_url"] == "ws://test/rpc"
    assert captured["resource_root"] == (tmp_path / "data" / "incoming")
    assert captured["app_service"] is service


def test_parse_args_help不初始化runtime(monkeypatch, capsys):
    async def fail_if_initialized(*args, **kwargs):
        raise AssertionError("runtime should not be initialized")

    monkeypatch.setattr(
        main_module,
        "_create_gateway_client",
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
        "apps.agent.src.application.agent_runtime",
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


def test_tui依赖和脚本归属当前app():
    project_root = Path(__file__).resolve().parents[3]
    requirements = (project_root / "apps/tui/requirements.txt").read_text()
    start_script = (project_root / "apps/tui/scripts/start.sh").read_text()

    assert "textual>=8.2.8,<9" in requirements
    assert "websockets" in requirements
    assert "openai" not in requirements
    assert "anthropic" not in requirements
    assert "apps.tui.src.main" in start_script
    assert "PYTHONPATH" in start_script
