"""Checks for CI wiring whose absence silently drops validation."""

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]


def workflow(name):
    return yaml.safe_load((ROOT / ".github/workflows" / name).read_text(encoding="utf-8"))


def test_services_cover_rela_mem_and_root_configuration_inputs():
    doc = workflow("apps.yml")
    events = doc.get("on", doc.get(True))
    for event in ("push", "pull_request"):
        paths = events[event]["paths"]
        assert {"icarus.toml", "bin/**", "infra/**", "apps/rela-mem/**"} <= set(paths)
    assert "rela-mem" in str(doc["jobs"]["contracts"])


def test_tui_has_a_real_application_test_job():
    path = ROOT / ".github/workflows/tui.yml"
    assert path.is_file(), "TUI must run its suite, not only compile"
    runs = "\n".join(step.get("run", "") for step in workflow("tui.yml")["jobs"]["test"]["steps"])
    assert "make test-tui" in runs


def test_backend_verifies_wheel_and_keeps_test_selection_in_the_app():
    runs = "\n".join(step.get("run", "") for step in workflow("backend-contracts.yml")["jobs"]["agent-gateway"]["steps"])
    assert "make check-package-agent" in runs
    assert "apps/agent/test/agent_app/plugins/memory/test_factory.py" not in runs


def test_webui_fast_checks_precede_browser_install_and_tests():
    doc = workflow("webui.yml")
    steps = doc["jobs"]["check"]["steps"]
    fast = next(i for i, step in enumerate(steps) if "pnpm lint" in step.get("run", ""))
    browser = next(i for i, step in enumerate(steps) if "playwright install" in step.get("run", ""))
    assert fast < browser
    assert doc["concurrency"]["cancel-in-progress"] is True


def test_syntax_checks_use_full_git_history_and_shared_checked_commands():
    steps = workflow("lint.yml")["jobs"]["syntax"]["steps"]
    checkout = next(step for step in steps if step.get("uses", "").startswith("actions/checkout@"))
    assert checkout["with"]["fetch-depth"] == 0
    runs = "\n".join(step.get("run", "") for step in steps)
    assert "scripts/ci/checks.py shell" in runs
    assert "scripts/ci/checks.py diff" in runs


def test_unified_gate_runs_without_path_filters_and_waits_for_every_task():
    doc = workflow("ci.yml")
    events = doc.get("on", doc.get(True))
    assert "paths" not in events["pull_request"]
    assert "paths" not in events["push"]
    jobs = doc["jobs"]
    gate = jobs["ci-required"]
    assert gate["if"] == "always()"
    assert set(gate["needs"]) == set(jobs) - {"ci-required"}
    assert any(step.get("run") == "python scripts/ci/check_results.py" for step in gate["steps"])
    assert jobs["images"]["needs"] == ["changes", "syntax", "backend", "services", "webui"]


def test_release_requires_identity_and_ci_before_loading_and_smoking_images():
    doc = workflow("publish-images.yml")
    assert doc["concurrency"]["group"] == "publish-runtime-images"
    assert doc["concurrency"]["cancel-in-progress"] is False
    assert doc["jobs"]["verify"]["uses"] == "./.github/workflows/ci.yml"
    assert doc["jobs"]["verify"]["with"]["full"] is True
    build = doc["jobs"]["build"]
    assert set(build["needs"]) == {"identity", "verify"}
    steps = build["steps"]
    index = next(i for i, step in enumerate(steps) if "smoke_image.py" in step.get("run", ""))
    push = next(i for i, step in enumerate(steps) if "docker push" in step.get("run", ""))
    assert index < push
    image = next(step for step in steps if step.get("uses", "").startswith("docker/build-push-action@"))
    assert image["with"]["load"] is True
    assert image["with"]["push"] is False
    assert "release.py promote" in str(doc["jobs"]["merge"]["steps"])


def test_external_actions_are_immutable_and_dependency_updates_target_feature():
    import re

    for path in (ROOT / ".github/workflows").glob("*.yml"):
        doc = workflow(path.name)
        for job in doc["jobs"].values():
            for step in job.get("steps", []):
                if action := step.get("uses"):
                    assert re.fullmatch(r"[\w-]+/[\w-]+@[a-f0-9]{40}", action), (path.name, action)
    config = yaml.safe_load((ROOT / ".github/dependabot.yml").read_text())
    assert config["updates"][0]["target-branch"] == "feature"
