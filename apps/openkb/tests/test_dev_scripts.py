import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[1]


def copy_script(tmp_path, name):
    app = tmp_path / "repo with spaces" / "apps" / "openkb"
    (app / "scripts").mkdir(parents=True)
    target = app / "scripts" / name
    shutil.copy2(APP / "scripts" / name, target)
    return app, target


def fake_executable(path, exit_code=0):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        "with open(os.environ['CALL_LOG'], 'a') as output:\n"
        "    record = {'args': sys.argv[1:], 'cwd': os.getcwd(),\n"
        "              'env': os.environ.get('UV_PROJECT_ENVIRONMENT')}\n"
        "    output.write(json.dumps(record) + '\\n')\n"
        f"sys.exit({exit_code})\n",
        encoding="utf-8",
    )
    path.chmod(0o755)


def run_script(script, tmp_path, log, *args, **environment):
    return subprocess.run(
        ["bash", str(script), *args], cwd=tmp_path,
        env={**os.environ, "CALL_LOG": str(log), **environment},
        capture_output=True, text=True,
    )


@pytest.mark.parametrize("mode", [(), ("--full",)])
def test_runner_uses_private_python_and_app_directory(tmp_path, mode):
    app, script = copy_script(tmp_path, "test.sh")
    log = tmp_path / "calls.jsonl"
    fake_executable(app / ".venv/bin/python")
    result = run_script(script, tmp_path, log, *mode)
    assert result.returncode == 0, result.stderr
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    assert calls[0]["cwd"] == str(app)
    assert calls[0]["args"][:2] == ["-m", "pytest"]
    if mode:
        assert calls[0]["args"][2:4] == ["tests", "-q"]
    else:
        assert "tests/test_api.py" in calls[0]["args"]
        assert "tests/test_remove.py" in calls[0]["args"]
        assert "tests/test_bundled_skills.py" in calls[0]["args"]
        assert "tests" not in calls[0]["args"]
    assert calls[1]["args"][:2] == ["-m", "compileall"]


def test_runner_preserves_failure_and_stops_before_compile(tmp_path):
    app, script = copy_script(tmp_path, "test.sh")
    log = tmp_path / "calls.jsonl"
    fake_executable(app / ".venv/bin/python", exit_code=7)
    result = run_script(script, tmp_path, log)
    assert result.returncode == 7
    assert len(log.read_text().splitlines()) == 1


@pytest.mark.parametrize("args", [("--unknown",), ("--full", "extra")])
def test_runner_rejects_invalid_arguments(tmp_path, args):
    app, script = copy_script(tmp_path, "test.sh")
    log = tmp_path / "calls.jsonl"
    fake_executable(app / ".venv/bin/python")
    result = run_script(script, tmp_path, log, *args)
    assert result.returncode == 2
    assert not log.exists()


def test_runner_reports_missing_environment(tmp_path):
    _, script = copy_script(tmp_path, "test.sh")
    result = run_script(script, tmp_path, tmp_path / "calls.jsonl")
    assert result.returncode == 1
    assert "icarus install openkb --dev" in result.stderr


def test_installer_uses_lock_extras_and_ignores_external_environment_override(tmp_path):
    app, script = copy_script(tmp_path, "install.sh")
    bin_dir = tmp_path / "tools"
    python = bin_dir / "python"
    fake_executable(python)
    fake_executable(bin_dir / "uv")
    log = tmp_path / "calls.jsonl"
    result = run_script(
        script, tmp_path, log, "--dev", PYTHON=str(python),
        PATH=str(bin_dir) + os.pathsep + os.environ["PATH"],
        UV_PROJECT_ENVIRONMENT=str(tmp_path / "wrong env"),
    )
    assert result.returncode == 0, result.stderr
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    assert calls[-1]["args"] == [
        "sync", "--locked", "--project", str(app), "--extra", "dev",
        "--extra", "api", "--python", str(python),
    ]
    assert calls[-1]["env"] == str(app / ".venv")
    assert not (app.parents[1] / ".venv").exists()
    assert not (app.parents[1] / ".env").exists()


def test_installer_rejects_non_dev_invocation(tmp_path):
    _, script = copy_script(tmp_path, "install.sh")
    result = run_script(script, tmp_path, tmp_path / "calls.jsonl")
    assert result.returncode == 2


def test_image_context_patterns_cover_nested_private_kb_data(tmp_path):
    private = (
        ".openkb/config.yaml", "kbs/personal/raw/private.pdf",
        "custom-kb/wiki/summary.md", "custom-kb/output/private.html",
    )
    for name in private:
        file = tmp_path / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.touch()
    excluded = set()
    for line in (APP / ".dockerignore").read_text().splitlines():
        pattern = line.strip()
        if not pattern or pattern.startswith("#"):
            continue
        for match in tmp_path.glob(pattern.rstrip("/")):
            if match.is_dir():
                excluded.update(file.relative_to(tmp_path).as_posix() for file in match.rglob("*"))
            else:
                excluded.add(match.relative_to(tmp_path).as_posix())
    assert set(private) <= excluded
