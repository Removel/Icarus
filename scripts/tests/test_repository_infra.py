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
    (tmp_path / ".env").write_text("USER_SETTING=keep\n", encoding="utf-8")
    control = IcarusControl(tmp_path, cwd=tmp_path, environ={})
    commands = []
    monkeypatch.setattr(control, "_run_checked", lambda command: commands.append(list(command)))

    def no_docker():
        raise AssertionError("A development-only service install must not need Docker")

    monkeypatch.setattr(control, "_check_docker", no_docker)
    assert control.run(["install", app, "--dev"]) == 0
    assert commands == [
        ["bash", str(tmp_path / f"apps/{app}/scripts/install.sh"), "--dev"],
        [str(tmp_path / "scripts/install-commands.sh")],
    ]
    assert (tmp_path / ".env").read_text() == "USER_SETTING=keep\n"
    assert not (tmp_path / ".venv").exists()


@pytest.mark.parametrize("app", ["mem0", "openkb"])
def test_failed_service_dev_install_does_not_continue_to_command_links(tmp_path, app, monkeypatch):
    (tmp_path / ".env").touch()
    control = IcarusControl(tmp_path, cwd=tmp_path, environ={})
    commands = []

    def fail(command):
        commands.append(list(command))
        raise ControlError("private install failed")

    monkeypatch.setattr(control, "_run_checked", fail)
    with pytest.raises(ControlError, match="private install failed"):
        control.run(["install", app, "--dev"])
    assert len(commands) == 1
    assert commands[0][1] == str(tmp_path / f"apps/{app}/scripts/install.sh")


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
    assert {entry["app"] for entry in matrix} == {"mem0", "openkb", "webui"}
    for entry in matrix:
        assert (ROOT / entry["context"]).is_dir()
        assert (ROOT / entry["dockerfile"]).is_file()
    runs = "\n".join(step.get("run", "") for step in job["steps"])
    assert "docker build" in runs
    assert "docker push" not in runs


def test_new_workflows_never_run_fork_code_with_write_permissions():
    import yaml

    for file in (ROOT / ".github/workflows").glob("*.yml"):
        doc = yaml.safe_load(file.read_text())
        events = doc.get("on", doc.get(True))
        assert "pull_request_target" not in events, file.name
        assert doc["permissions"] == {"contents": "read"}, file.name
        for job in doc["jobs"].values():
            assert "continue-on-error" not in job
            assert "secrets." not in str(job)
            for step in job["steps"]:
                if step.get("uses", "").startswith("actions/checkout@"):
                    assert step["with"]["persist-credentials"] is False
