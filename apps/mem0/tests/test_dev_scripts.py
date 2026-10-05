import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest


APP = Path(__file__).resolve().parents[1]


def copy_script(tmp_path, name):
    app = tmp_path / "repo with spaces" / "apps" / "mem0"
    scripts = app / "scripts"
    scripts.mkdir(parents=True)
    target = scripts / name
    shutil.copy2(APP / "scripts" / name, target)
    return app, target


def fake_python(app, log, exit_code=0):
    executable = app / ".venv" / "bin" / "python"
    executable.parent.mkdir(parents=True)
    executable.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        "with open(os.environ['CALL_LOG'], 'a') as output:\n"
        "    record = {'args': sys.argv[1:], 'cwd': os.getcwd(),\n"
        "              'config': os.environ.get('PIP_CONFIG_FILE')}\n"
        "    output.write(json.dumps(record) + '\\n')\n"
        f"sys.exit({exit_code})\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    return executable


def run_script(script, tmp_path, log, *args, **environment):
    return subprocess.run(
        ["bash", str(script), *args], cwd=tmp_path,
        env={**os.environ, "CALL_LOG": str(log), **environment},
        text=True, capture_output=True,
    )


@pytest.mark.parametrize("mode", [(), ("--full",)])
def test_runner_uses_private_environment_and_app_working_directory(tmp_path, mode):
    app, script = copy_script(tmp_path, "test.sh")
    log = tmp_path / "calls.jsonl"
    fake_python(app, log)
    result = run_script(script, tmp_path, log, *mode)
    assert result.returncode == 0, result.stderr
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    assert calls[0]["cwd"] == str(app)
    assert calls[0]["args"][:2] == ["-m", "pytest"]
    assert calls[1]["args"][:2] == ["-m", "compileall"]
    if mode:
        assert calls[0]["args"][2:4] == ["tests", "-q"]
    else:
        assert "tests/memory/test_main.py" in calls[0]["args"]
        assert "tests/memory/test_storage.py" in calls[0]["args"]
        assert "tests" not in calls[0]["args"]


def test_runner_returns_pytest_failure_without_compiling(tmp_path):
    app, script = copy_script(tmp_path, "test.sh")
    log = tmp_path / "calls.jsonl"
    fake_python(app, log, exit_code=7)
    result = run_script(script, tmp_path, log)
    assert result.returncode == 7
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    assert len(calls) == 1
    assert calls[0]["args"][:2] == ["-m", "pytest"]


@pytest.mark.parametrize("args", [("--unknown",), ("--full", "extra")])
def test_runner_rejects_invalid_arguments(tmp_path, args):
    app, script = copy_script(tmp_path, "test.sh")
    log = tmp_path / "calls.jsonl"
    fake_python(app, log)
    result = run_script(script, tmp_path, log, *args)
    assert result.returncode == 2
    assert not log.exists()


def test_runner_reports_missing_environment(tmp_path):
    _, script = copy_script(tmp_path, "test.sh")
    result = run_script(script, tmp_path, tmp_path / "calls.jsonl")
    assert result.returncode == 1
    assert "icarus install mem0 --dev" in result.stderr


def test_installer_uses_local_manifest_without_creating_root_environment(tmp_path):
    app, script = copy_script(tmp_path, "install.sh")
    shutil.copy2(APP / "requirements-dev.txt", app / "requirements-dev.txt")
    log = tmp_path / "calls.jsonl"
    executable = fake_python(app, log)
    result = run_script(script, tmp_path, log, "--dev", PYTHON=str(executable))
    assert result.returncode == 0, result.stderr
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    assert calls[-1]["cwd"] == str(app)
    assert calls[-1]["args"] == [
        "-I", "-m", "pip", "--isolated", "install", "-r", str(app / "requirements-dev.txt")
    ]
    assert not (app.parents[1] / ".venv").exists()
    assert not (app.parents[1] / ".env").exists()


def test_installer_rejects_non_dev_invocation(tmp_path):
    _, script = copy_script(tmp_path, "install.sh")
    result = run_script(script, tmp_path, tmp_path / "calls.jsonl")
    assert result.returncode == 2


def test_installer_isolates_pip_destination_configuration(tmp_path):
    app, script = copy_script(tmp_path, "install.sh")
    shutil.copy2(APP / "requirements-dev.txt", app / "requirements-dev.txt")
    log = tmp_path / "calls.jsonl"
    executable = fake_python(app, log)
    config = tmp_path / "pip.conf"
    config.write_text("[install]\ntarget = /outside/app\n", encoding="utf-8")
    result = run_script(
        script, tmp_path, log, "--dev", PYTHON=str(executable),
        PIP_TARGET=str(tmp_path / "outside"), PIP_PREFIX=str(tmp_path / "wrong"),
        PIP_CONFIG_FILE=str(config),
    )
    assert result.returncode == 0, result.stderr
    pip_call = json.loads(log.read_text().splitlines()[-1])
    assert "--isolated" in pip_call["args"]
    assert pip_call["config"] == os.devnull


def test_installer_refuses_foreign_interpreter_at_private_env_path(tmp_path):
    import sys

    app, script = copy_script(tmp_path, "install.sh")
    (app / ".venv/bin").mkdir(parents=True)
    (app / ".venv/bin/python").symlink_to(sys.executable)
    result = run_script(script, tmp_path, tmp_path / "calls.jsonl", "--dev")
    assert result.returncode == 1
    assert "private virtual environment" in result.stderr
