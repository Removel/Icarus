"""Container state errors must fail smoke checks; teardown owns only its resources."""

import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]


def smoke_module():
    spec = importlib.util.spec_from_file_location("ci_smoke", ROOT / "scripts/ci/smoke_image.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("state", [{"Status": "exited"}, {"Status": "running", "Health": {"Status": "unhealthy"}}])
def test_exited_or_unhealthy_images_cannot_pass(monkeypatch, state):
    smoke = smoke_module()
    monkeypatch.setattr(smoke, "docker", lambda *args: json.dumps([{"State": state}]))
    with pytest.raises(RuntimeError, match="startup failed"):
        smoke.wait_healthy("fixture", 1)


def test_smoke_requires_a_healthcheck_not_just_running(monkeypatch):
    smoke = smoke_module()
    clock = iter([0, 0, 2])
    monkeypatch.setattr(smoke.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(smoke.time, "sleep", lambda _: None)
    monkeypatch.setattr(smoke, "docker", lambda *args: json.dumps([{"State": {"Status": "running"}}]))
    with pytest.raises(RuntimeError, match="deadline exceeded"):
        smoke.wait_healthy("fixture", 1)


def test_app_fixtures_use_only_local_disposable_endpoints():
    for app in ["webui", "mem0", "openkb", "rela-mem"]:
        directory = "tests" if app in {"mem0", "openkb"} else "test"
        fixture = json.loads((ROOT / "apps" / app / directory / "image-smoke.json").read_text())
        for value in fixture.get("env", {}).values():
            if value.startswith(("http://", "https://")):
                assert value.startswith("http://127.0.0.1:")


def test_failed_smoke_cleans_only_created_containers_and_network(monkeypatch):
    smoke = smoke_module()
    commands = []
    def docker(*args):
        commands.append(args)
        return "created"
    monkeypatch.setattr(smoke, "docker", docker)
    monkeypatch.setattr(smoke, "wait_healthy", lambda *args: (_ for _ in ()).throw(RuntimeError("bad image")))
    monkeypatch.setattr(smoke.subprocess, "run", lambda args, **kwargs: commands.append(tuple(args[1:])))
    monkeypatch.setattr("sys.argv", ["smoke_image.py", "webui", "local:test"])
    with pytest.raises(RuntimeError, match="bad image"):
        smoke.main()
    network = next(command[-1] for command in commands if command[:2] == ("network", "create"))
    assert ("network", "rm", network) in commands
    assert ("rm", "-f", "-v", network + "-app") in commands
    assert all("prune" not in command for command in commands)
