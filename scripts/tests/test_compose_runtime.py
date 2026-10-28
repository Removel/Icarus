from __future__ import annotations

from dataclasses import replace
import importlib
import os
from pathlib import Path
import subprocess
import sys

import pytest

CONTROL_DIR = Path(__file__).resolve().parents[1] / "icarus"
if str(CONTROL_DIR) not in sys.path:
    sys.path.insert(0, str(CONTROL_DIR))

from registry import Component, Registry


@pytest.fixture
def api():
    try:
        module = importlib.import_module("compose_runtime")
    except ModuleNotFoundError as error:
        if error.name != "compose_runtime":
            raise
        module = None
    return module


class DockerBoundary:
    """Only the external Docker invocation is substituted, never runtime logic."""
    def __init__(self):
        self.calls = []
        self.plugin = True
        self.standalone = True
        self.network = False
        self.output = ""
        self.fail_action = None

    def __call__(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        if argv == ["docker", "compose", "version"]:
            return subprocess.CompletedProcess(argv, 0 if self.plugin else 1, "", "")
        if argv == ["docker-compose", "version"]:
            if not self.standalone:
                raise FileNotFoundError("missing executable")
            return subprocess.CompletedProcess(argv, 0, "", "")
        if argv[:3] == ["docker", "network", "inspect"]:
            return subprocess.CompletedProcess(argv, 0 if self.network else 1, "", "")
        if argv[:3] == ["docker", "network", "create"]:
            self.network = True
        code = 1 if self.fail_action and self.fail_action in argv else 0
        return subprocess.CompletedProcess(argv, code, self.output, "PRIVATE_SECRET")


def test_runtime_module_contract(api):
    assert api is not None, "ComposeRuntime must implement the declared Docker boundary"


@pytest.fixture
def setup(tmp_path, api):
    compose = tmp_path / "infra/database.yaml"
    compose.parent.mkdir()
    compose.write_text("services: {}\n")
    component = Component("storage", "middleware", "local", "compose",
                          compose="infra/database.yaml", compose_project="isolated-storage",
                          data_dir="services/existing", host="127.0.0.1", port=15432)
    boundary = DockerBoundary()
    runtime = api.ComposeRuntime(tmp_path, tmp_path / "data", {"PATH": "/bin"},
                                 Registry(1, {component.name: component}), runner=boundary)
    return runtime, component, boundary


def test_lazy_remote_operations_never_invoke_docker(tmp_path, api):
    boundary = DockerBoundary()
    component = Component("away", "app", "remote", "compose")
    runtime = api.ComposeRuntime(tmp_path, None, {}, Registry(1, {"away": component}), boundary)
    assert boundary.calls == []
    runtime.build(component)
    runtime.up(component)
    runtime.down(component)
    assert runtime.logs(component) == 0
    assert runtime.containers(component) == []
    assert boundary.calls == []


def test_up_scopes_project_file_and_copies_injected_environment(setup):
    runtime, component, boundary = setup
    injected = {"ICARUS_BIND_PORT": "25432", "DATABASE": "bootstrap"}
    runtime.up(component, injected)
    assert boundary.calls, "up must invoke the Compose subprocess boundary"
    argv, options = boundary.calls[-1]
    assert argv == ["docker", "compose", "--env-file", os.devnull, "-p", "isolated-storage",
                    "-f", str(runtime.repo_root / "infra/database.yaml"), "up", "-d"]
    assert options["env"]["ICARUS_BIND_PORT"] == "25432"
    assert options["env"]["ICARUS_DATA_DIR"] == str(runtime.data_dir)
    assert injected == {"ICARUS_BIND_PORT": "25432", "DATABASE": "bootstrap"}
    assert runtime.environ == {"PATH": "/bin"}
    assert (runtime.data_dir / "services/existing").is_dir()
    assert not any(argv[1:2] == ["network"] for argv, _ in boundary.calls)


def test_network_is_only_ensured_for_shared_start_and_data_survives_down(setup):
    runtime, component, boundary = setup
    component = replace(component, shared_network=True, network="isolated-net")
    runtime.build(component)
    assert not any("network" in argv for argv, _ in boundary.calls)
    runtime.up(component)
    assert ["docker", "network", "inspect", "isolated-net"] in [a for a, _ in boundary.calls]
    assert ["docker", "network", "create", "isolated-net"] in [a for a, _ in boundary.calls]
    sentinel = runtime.data_dir / "services/existing/keep"
    sentinel.write_text("owned data")
    runtime.up(component)
    runtime.down(component)
    assert sentinel.read_text() == "owned data"
    assert sum(a == ["docker", "network", "create", "isolated-net"] for a, _ in boundary.calls) == 1
    assert boundary.calls[-1][0][-1] == "down"
    assert "-v" not in boundary.calls[-1][0]


def test_explicit_image_action_selects_pull_without_parsing_yaml(setup):
    runtime, component, boundary = setup
    runtime.build(replace(component, image_action="pull"))
    assert boundary.calls[-1][0][-1] == "pull"
    runtime.build(replace(component, kind="app", image_action="build"))
    assert boundary.calls[-1][0][-1] == "build"



def test_explicit_envfile_does_not_fall_back_to_repository(setup):
    runtime, component, boundary = setup
    (runtime.repo_root / ".env").write_text("PRIVATE=value\n")
    selected = runtime.repo_root / "isolated.env"
    selected.write_text("SYNTHETIC=value\n")
    runtime.environ["ICARUS_ENV_FILE"] = str(selected)
    command = runtime.command(component, "config")
    assert command[command.index("--env-file") + 1] == str(selected)


def test_env_file_and_standalone_fallback(setup):
    runtime, component, boundary = setup
    (runtime.repo_root / ".env").write_text("EXAMPLE=value\n")
    boundary.plugin = False
    runtime.build(component)
    argv = boundary.calls[-1][0]
    assert argv[:3] == ["docker-compose", "--env-file", str(runtime.repo_root / ".env")]
    assert ["docker-compose", "version"] in [a for a, _ in boundary.calls]


def test_wrapper_keeps_action_first_and_receives_compose_scope(setup):
    runtime, component, boundary = setup
    component = replace(component, compose_command=("python3", "apps/custom/compose.py"))
    runtime.up(component, {"CUSTOM": "yes"})
    argv, options = boundary.calls[-1]
    assert argv == ["python3", "apps/custom/compose.py", "up", "-d"]
    assert options["cwd"] == runtime.repo_root
    assert options["env"]["COMPOSE_PROJECT_NAME"] == "isolated-storage"
    assert options["env"]["COMPOSE_FILE"] == str(runtime.repo_root / "infra/database.yaml")
    assert options["env"]["CUSTOM"] == "yes"
    assert not any(a == ["docker", "compose", "version"] for a, _ in boundary.calls)


def test_containers_are_project_and_file_scoped_and_skip_malformed_rows(setup):
    runtime, component, boundary = setup
    boundary.output = '{"State":"running","HealthStatus":"healthy"}\nnot json\n[]\n'
    assert runtime.containers(component) == [{"State": "running", "HealthStatus": "healthy"}]
    argv = boundary.calls[-1][0]
    assert argv == ["docker", "ps", "-a", "--filter",
                    "label=com.docker.compose.project=isolated-storage", "--filter",
                    f"label=com.docker.compose.project.config_files={runtime.repo_root / 'infra/database.yaml'}",
                    "--format", "{{json .}}"]


def test_errors_are_clear_and_do_not_leak_subprocess_secrets(setup, api):
    runtime, component, boundary = setup
    with pytest.raises(api.ComposeError, match="compose"):
        runtime.up(replace(component, compose="missing.yaml"))
    assert boundary.calls == []
    boundary.plugin = False
    boundary.standalone = False
    with pytest.raises(api.ComposeError, match="Compose"):
        runtime.build(component)
    boundary.plugin = True
    boundary.fail_action = "up"
    with pytest.raises(api.ComposeError) as error:
        runtime.up(component)
    assert "storage" in str(error.value)
    assert "PRIVATE_SECRET" not in str(error.value)


def test_logs_stream_boundary_and_return_exit_status(setup):
    runtime, component, boundary = setup
    boundary.fail_action = "logs"
    assert runtime.logs(component, follow=False) == 1
    argv, options = boundary.calls[-1]
    assert argv[-1] == "logs"
    assert not options.get("capture_output", False)
