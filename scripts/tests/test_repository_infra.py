from pathlib import Path
import subprocess

import pytest
import sys


ROOT = Path(__file__).resolve().parents[2]
CONTROL_DIR = ROOT / "scripts" / "icarus"
if str(CONTROL_DIR) not in sys.path:
    sys.path.insert(0, str(CONTROL_DIR))
from main import ControlError, IcarusControl


def git(*args):
    return subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout


def test_snapshot_checkout_uses_lf_without_removing_trailing_space():
    attributes = git(
        "check-attr", "eol", "whitespace", "--",
        "apps/tui/test/__snapshots__/probe.svg",
    )
    assert "eol: lf" in attributes
    assert "whitespace: -trailing-space" in attributes


@pytest.mark.parametrize("extension", ["png", "webp", "jpg", "pdf", "woff2"])
def test_binary_assets_are_not_normalized_as_text(extension):
    attributes = git("check-attr", "text", "diff", "--", f"probe.{extension}")
    assert "text: unset" in attributes
    assert "diff: unset" in attributes


def test_generated_assets_and_test_caches_stay_untracked():
    paths = ["apps/agent/.pytest_cache/probe", "apps/tui/node_modules/probe.js"]
    assert set(git("check-ignore", "--no-index", *paths).splitlines()) == set(paths)


def test_nested_service_data_remains_private_after_app_ignore_consolidation():
    paths = [
        "apps/openkb/custom-kb/raw/private.pdf",
        "apps/openkb/custom-kb/wiki/summary.md",
        "apps/openkb/custom-kb/.openkb/config.yaml",
        "apps/openkb/custom-kb/output/private.html",
        "apps/openkb/custom-kb/kbs/private.md",
        "apps/mem0/custom/qdrant_storage/private.bin",
        "apps/mem0/custom/test-db/private.bin",
        "apps/mem0/custom/history.db",
    ]
    ignored = git("check-ignore", "--no-index", *paths).splitlines()
    assert set(ignored) == set(paths)


@pytest.mark.parametrize("app", ["mem0", "openkb"])
def test_service_dev_install_prepares_local_env_without_docker_or_overwriting_env(tmp_path, app, monkeypatch):
    import shutil
    shutil.copy2(ROOT / "icarus.toml", tmp_path / "icarus.toml")
    (tmp_path / ".env").write_text("USER_SETTING=keep\n", encoding="utf-8")
    control = IcarusControl(tmp_path, cwd=tmp_path, environ={})
    commands = []
    monkeypatch.setattr(control, "_run_checked", lambda command, environment=None: commands.append(list(command)) or 0)
    assert control.run(["install", app, "--dev"]) == 0
    assert commands == [["bash", str(tmp_path / f"apps/{app}/scripts/install.sh"), "--dev"]]
    assert (tmp_path / ".env").read_text() == "USER_SETTING=keep\n"
    assert not (tmp_path / ".venv").exists()


@pytest.mark.parametrize("app", ["mem0", "openkb"])
def test_failed_service_dev_install_does_not_continue_to_command_links(tmp_path, app, monkeypatch):
    import shutil
    shutil.copy2(ROOT / "icarus.toml", tmp_path / "icarus.toml")
    control = IcarusControl(tmp_path, cwd=tmp_path, environ={})
    commands = []
    def fail(command, environment=None):
        commands.append(list(command))
        raise ControlError("private install failed")
    monkeypatch.setattr(control, "_run_checked", fail)
    with pytest.raises(ControlError, match="private install failed"):
        control.run(["install", app, "--dev"])
    assert len(commands) == 1


def test_infra_compose_files_preserve_data_paths_and_network():
    import yaml
    for name, image in (("postgres", "pgvector/pgvector:pg17"), ("falkordb", "falkordb/falkordb:v4.22.1")):
        path = ROOT / "infra" / name / "compose.yaml"
        assert path.exists(), f"{name} must have independently managed infrastructure"
        doc = yaml.safe_load(path.read_text())
        assert set(doc["services"]) == {name}
        assert doc["networks"]["icarus"]["external"] is True
        assert doc["networks"]["icarus"]["name"] == "${ICARUS_NETWORK:-icarus}"
        service = doc["services"][name]
        assert service["image"] == image
        assert f"services/rela-mem/{name}" in str(service["volumes"])
        assert service["healthcheck"]


def test_rela_mem_compose_is_app_only_and_connection_injectable():
    import yaml
    compose = yaml.safe_load((ROOT / "apps/rela-mem/docker-compose.yaml").read_text())
    assert set(compose["services"]) == {"rela-mem"}
    assert compose["networks"]["icarus"]["external"] is True
    app = compose["services"]["rela-mem"]
    assert compose["networks"]["icarus"]["name"] == "${ICARUS_NETWORK:-icarus}"
    assert app["image"] == "${RELA_MEM_IMAGE:-rela-mem:dev}"
    assert "depends_on" not in app
    for key in ("RELA_MEM_PG_HOST", "RELA_MEM_PG_PORT", "RELA_MEM_PG_DATABASE", "RELA_MEM_PG_USER", "RELA_MEM_PG_SCHEMA", "RELA_MEM_FALKORDB_HOST", "RELA_MEM_FALKORDB_PORT", "RELA_MEM_FALKORDB_GRAPH"):
        assert key in app["environment"]



@pytest.mark.parametrize("app", ["mem0", "openkb"])
@pytest.mark.parametrize("action", ["up", "pull", "down"])
def test_app_compose_wrapper_preserves_process_precedence_scope_and_action_first(tmp_path, app, action):
    import json
    import os
    import shutil
    repo = tmp_path / "repo with spaces"
    script = repo / "apps" / app / "scripts/icarus_compose.py"
    script.parent.mkdir(parents=True)
    shutil.copy2(ROOT / "apps" / app / "scripts/icarus_compose.py", script)
    helpers = repo / "scripts/icarus"
    helpers.mkdir(parents=True)
    shutil.copy2(ROOT / "scripts/icarus/environment.py", helpers)
    (repo / "apps/agent").mkdir(parents=True)
    (repo / "apps/agent/settings.json").write_text("{}")
    token = "synthetic-process-token"
    (repo / ".env").write_text(f"ICARUS_DATA_DIR=/file-data\nICARUS_{app.upper()}_PORT=1111\nICARUS_{app.upper()}_API_TOKEN=file-token\nICARUS_MEM0_API_KEY=file-key\n")
    tools = tmp_path / "tools"
    tools.mkdir()
    log = tmp_path / "compose.json"
    docker = tools / "docker-compose"
    docker.write_text("#!/usr/bin/env python3\nimport json,os,sys\nfrom pathlib import Path\nPath(os.environ['CALL_LOG']).write_text(json.dumps({'argv':sys.argv[1:],'port':os.environ.get('ICARUS_" + app.upper() + "_PORT'),'token':os.environ.get('ICARUS_" + app.upper() + "_API_TOKEN'),'key':os.environ.get('ICARUS_MEM0_API_KEY')}))\n")
    docker.chmod(0o755)
    custom = repo / "custom.yaml"
    custom.touch()
    environment = {**os.environ, "PATH": str(tools) + os.pathsep + os.environ["PATH"], "CALL_LOG": str(log),
                   "ICARUS_DATA_DIR": str(tmp_path / "data"), "COMPOSE_PROJECT_NAME": "test-project", "COMPOSE_FILE": str(custom),
                   f"ICARUS_{app.upper()}_PORT": "2222"}
    if action == "up":
        environment.update(ICARUS_MEM0_API_KEY=token, ICARUS_MEM0_JWT_SECRET="fixture-jwt", ICARUS_MEM0_POSTGRES_PASSWORD="fixture-db",
                           ICARUS_MEM0_LLM_API_KEY="fixture-llm", ICARUS_OPENKB_API_TOKEN=token, ICARUS_OPENKB_LLM_API_KEY="fixture-llm")
    result = subprocess.run([sys.executable, str(script), action, *( ["-d"] if action == "up" else [])],
                            env=environment, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    call = json.loads(log.read_text())
    assert call["port"] == "2222"
    argv = call["argv"]
    assert argv[argv.index("-f") + 1] == str(custom)
    assert argv[argv.index("-p") + 1] == "test-project"
    assert argv[-2:] == ["up", "-d"] if action == "up" else argv[-1] == action
    if action == "up":
        assert call["token"] == token if app == "openkb" else call["key"] == token


def test_app_compose_ports_are_environment_parameterized():
    import yaml
    mem0 = yaml.safe_load((ROOT / "apps/mem0/server/docker-compose.yaml").read_text())["services"]
    openkb = yaml.safe_load((ROOT / "apps/openkb/docker-compose.yaml").read_text())["services"]
    for app, service, variable in ((mem0, "mem0", "ICARUS_MEM0_PORT"),
                                    (openkb, "openkb", "ICARUS_OPENKB_PORT")):
        assert variable in app[service]["ports"][0]


def test_service_workflow_covers_root_control_and_runs_the_app_entrypoints():
    import yaml

    doc = yaml.safe_load((ROOT / ".github/workflows/apps.yml").read_text())
    assert doc["permissions"] == {"contents": "read"}
    events = doc.get("on", doc.get(True))
    for event in ("push", "pull_request"):
        patterns = events[event]["paths"]
        for path in ("Makefile", "scripts/**", "apps/mem0/**", "apps/openkb/**"):
            assert path in patterns
    job = doc["jobs"]["contracts"]
    assert job["strategy"]["matrix"]["app"] == ["mem0", "openkb"]
    runs = "\n".join(step.get("run", "") for step in job["steps"])
    assert "make test-${{ matrix.app }}" in runs
    assert "scripts/install.sh --dev" in runs
    assert "continue-on-error" not in job


def test_image_workflow_builds_app_owned_inputs_without_publishing():
    import yaml

    doc = yaml.safe_load((ROOT / ".github/workflows/images.yml").read_text())
    assert doc["permissions"] == {"contents": "read"}
    job = doc["jobs"]["build"]
    matrix = job["strategy"]["matrix"]["include"]
    assert {entry["app"] for entry in matrix} == {"mem0", "openkb", "rela-mem", "webui"}
    for entry in matrix:
        assert (ROOT / entry["context"]).is_dir()
        assert (ROOT / entry["dockerfile"]).is_file()
    build_steps = [step for step in job["steps"]
                   if step.get("uses", "").startswith("docker/build-push-action@")]
    assert build_steps
    assert build_steps[0]["with"]["push"] is False
    assert all("docker push" not in str(step) for step in job["steps"])


def test_new_workflows_never_run_fork_code_with_write_permissions():
    import yaml

    for file in (ROOT / ".github/workflows").glob("*.yml"):
        doc = yaml.safe_load(file.read_text())
        events = doc.get("on", doc.get(True))
        assert "pull_request_target" not in events, file.name
        pull_request_triggered = (
            "pull_request" in events
            if isinstance(events, (dict, list, tuple, str))
            else False
        )
        assert doc["permissions"] == {"contents": "read"}, file.name
        for job in doc["jobs"].values():
            assert "continue-on-error" not in job
            if pull_request_triggered:
                assert "secrets." not in str(job)
            for step in job["steps"]:
                if step.get("uses", "").startswith("actions/checkout@"):
                    assert step["with"]["persist-credentials"] is False
