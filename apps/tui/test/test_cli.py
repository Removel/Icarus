from pathlib import Path

import pytest

from apps.tui.src import main as main_module


def test_run_app_使用启动目录透传session_id并返回textual结果(monkeypatch, tmp_path):
    captured = {}

    class ServiceStub:
        def __init__(self, workspace_path, *, session_id=None):
            captured["service_workspace"] = workspace_path
            captured["session_id"] = session_id

    class AppStub:
        return_code = None

        def __init__(self, *, service, workspace_path):
            captured["app_service"] = service
            captured["app_workspace"] = workspace_path

        def run(self):
            return 7

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(main_module, "AgentRuntimeService", ServiceStub)
    monkeypatch.setattr(main_module, "IcarusTextualApp", AppStub)

    result = main_module.run_app(["--session-id", "demo"])

    assert result == 7
    assert captured["service_workspace"] == tmp_path.resolve()
    assert captured["app_workspace"] == tmp_path.resolve()
    assert captured["session_id"] == "demo"
    assert isinstance(captured["app_service"], ServiceStub)


def test_parse_args_help不初始化runtime(monkeypatch, capsys):
    def fail_if_initialized(*args, **kwargs):
        raise AssertionError("runtime should not be initialized")

    monkeypatch.setattr(main_module, "AgentRuntimeService", fail_if_initialized)

    with pytest.raises(SystemExit) as exit_info:
        main_module.main(["--help"])

    assert exit_info.value.code == 0
    assert "Icarus Agent terminal client" in capsys.readouterr().out


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
