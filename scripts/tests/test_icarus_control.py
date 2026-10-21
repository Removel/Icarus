"""CLI facade regression tests; process identity safety lives in test_process_runtime.

The old facade's monkeypatched PID/ps tests moved to the real /proc-backed adapter
suite: foreign CWD/marker-prefix/port, orphan adoption, PID starttime, external
ownership, custom log and ephemeral address safety. End-to-end behavior is also
covered by test_lifecycle_cli rather than retaining duplicate private methods.
"""
from contextlib import redirect_stdout
import io
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/icarus"))
from configuration import load_configuration
from environment import expand_braced_variables, read_env_file
from graph import required_closure
from lifecycle import Lifecycle
from main import REPO_ROOT, ControlError, IcarusControl
from probes import ProbeResult


def control(tmp_path):
    shutil.copy2(ROOT / "icarus.toml", tmp_path / "icarus.toml")
    return IcarusControl(tmp_path, cwd=tmp_path, environ={})


def test_default_repository_root_and_default_stack_required_closure():
    assert REPO_ROOT == ROOT
    registry = load_configuration(ROOT)
    order = required_closure(registry, registry.default_stack())
    assert {"rela-mem", "postgres", "falkordb", "mem0", "openkb", "gateway", "webui", "tui", "agent"} <= set(order)
    assert "argus" not in registry.names()
    assert "mem0-postgres" not in order
    for dependency in ("mem0", "openkb", "agent"):
        assert order.index(dependency) < order.index("gateway")


def test_agent_has_no_independent_lifecycle(tmp_path):
    instance = control(tmp_path)
    for action in ("start", "stop"):
        with pytest.raises(ControlError, match="inside gateway"):
            instance.run([action, "agent"])


def test_legacy_tui_and_explicit_tui_use_same_foreground_path(tmp_path, monkeypatch):
    instance = control(tmp_path)
    calls = []
    class Runtime:
        def run_foreground(self, c, args, cwd=None):
            calls.append((c.name, args, cwd))
            return 0
    monkeypatch.setattr(instance, "_runtime", lambda registry: Runtime())
    assert instance.run(["--session-id", "old"]) == 0
    assert instance.run(["tui", "--session-id", "new"]) == 0
    assert calls == [("tui", ["--session-id", "old"], tmp_path), ("tui", ["--session-id", "new"], tmp_path)]


def test_app_install_uses_registry_private_installer_without_creating_env(tmp_path, monkeypatch):
    instance = control(tmp_path)
    calls = []
    monkeypatch.setattr(instance, "_run_checked", lambda argv, env=None: calls.append(argv) or 0)
    assert instance.run(["install", "webui"]) == 0
    assert calls == [["bash", str(tmp_path / "apps/webui/scripts/install.sh")]]
    assert not (tmp_path / ".env").exists() and not (tmp_path / ".venv").exists()


@pytest.mark.parametrize("rows,ready,state", [
    ([{"State": "running"}], True, "running"),
    ([{"State": "running", "HealthStatus": "healthy"}] * 2 + [{"State": "running", "HealthStatus": "starting"}], True, "starting"),
    ([{"State": "exited"}], False, "stopped"),
    ([{"State": "running", "HealthStatus": "healthy"}] * 3, True, "running"),
    ([{"State": "running", "HealthStatus": "unhealthy"}] * 3, True, "unhealthy"),
])
def test_compose_status_requires_complete_healthy_group(rows, ready, state):
    registry = load_configuration(ROOT)
    class Compose:
        def containers(self, c):
            return rows
    lifecycle = Lifecycle(registry, compose=Compose(), process=object(),
                          probe_func=lambda c, e: ProbeResult(ready, "reachable" if ready else "unreachable", ""))
    assert lifecycle.status(["mem0"])[0].state == state


def test_compose_inspection_error_is_unknown_not_stopped():
    class Compose:
        def containers(self, c):
            raise RuntimeError("container inspection unavailable")
    lifecycle = Lifecycle(load_configuration(ROOT), compose=Compose(), process=object())
    row = lifecycle.status(["mem0"])[0]
    assert row.state == "unknown" and "unavailable" in row.detail


def test_embedded_status_mirrors_remote_owner_without_local_dependencies():
    registry = load_configuration(ROOT, overrides={"gateway": {"mode": "remote"}})
    lifecycle = Lifecycle(registry, compose=object(), process=object(),
                          probe_func=lambda c, e: ProbeResult(True, "reachable", "test endpoint"))
    row = lifecycle.status(["agent"])[0]
    assert row.state == "reachable" and row.mode == "remote" and "embedded in gateway" in row.detail


def test_env_reader_preserves_quoted_spaces_dollar_and_known_expansion(tmp_path):
    path = tmp_path / ".env"
    path.write_text("A='value with $ and spaces'\nB=plain # comment\nexport C=third\n")
    assert read_env_file(path) == {"A": "value with $ and spaces", "B": "plain", "C": "third"}
    assert expand_braced_variables("${ROOT}/data/${UNKNOWN}", {"ROOT": "/tmp"}) == "/tmp/data/${UNKNOWN}"



def test_explicit_envfile_isolated_from_repository_and_process_wins(tmp_path, monkeypatch):
    import environment
    helper = getattr(environment, "merged_environment", None)
    assert helper is not None, "root and wrappers must share explicit envfile precedence"
    (tmp_path / ".env").write_text("SECRET=file-secret\nONLY_REPO=must-not-read\n")
    alternate = tmp_path / "alternate.env"
    alternate.write_text("SECRET=alternate-secret\nALTERNATE=yes\n")
    values = helper(tmp_path, {"ICARUS_ENV_FILE": str(alternate), "SECRET": "process-secret"})
    assert values["SECRET"] == "process-secret"
    assert values["ALTERNATE"] == "yes" and "ONLY_REPO" not in values




@pytest.mark.parametrize("command", ["install", "start", "stop", "status", "logs", "tui"])
def test_command_help_never_starts_or_loads_configuration(tmp_path, command, capsys):
    instance = IcarusControl(tmp_path, environ={})
    assert instance.run([command, "--help"]) == 0
    assert "Usage:" in capsys.readouterr().out



def test_public_command_installer_remains_idempotent(tmp_path):
    bindir = tmp_path / "bin"
    for _ in range(2):
        result = subprocess.run([str(ROOT / "scripts/install-commands.sh")], cwd=ROOT,
                                env={**os.environ, "ICARUS_BIN_DIR": str(bindir)}, capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
    assert (bindir / "icarus").resolve() == ROOT / "bin/icarus"
    assert (bindir / "icarus-gateway").resolve() == ROOT / "bin/icarus-gateway"
