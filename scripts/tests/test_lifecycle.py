"""Generic orchestration tests; Docker itself is the substituted boundary."""
from dataclasses import replace
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "icarus"))
from lifecycle import Lifecycle
from probes import ProbeResult
from registry import Component, Dependency, Probe, Registry


class Compose:
    def __init__(self):
        self.calls = []
        self.started = set()
    def containers(self, c):
        return [{"State": "running", "HealthStatus": "healthy"}] if c.name in self.started else []
    def up(self, c, environment=None):
        self.calls.append(("up", c.name, environment))
        self.started.add(c.name)
    def down(self, c, environment=None):
        self.calls.append(("down", c.name, environment))
        self.started.remove(c.name)
    def build(self, c, environment=None):
        self.calls.append((c.image_action, c.name, environment))
    def logs(self, c, follow, environment=None):
        self.calls.append(("logs", c.name, environment))
        return 0


class Process:
    environ = {}
    def __init__(self):
        self.calls = []
        self.running = set()
    def effective_component(self, c):
        return c
    def status(self, c):
        return type("State", (), {"state": "running" if c.name in self.running else "stopped", "detail": "-"})()
    def spawn(self, c, args=(), log_path=None, environment=None):
        self.calls.append(("spawn", c.name, environment))
        self.running.add(c.name)
    def stop(self, c):
        self.calls.append(("stop", c.name, None))
        self.running.discard(c.name)
    def foreground(self, c, args=(), environment=None, cwd=None):
        self.calls.append(("foreground", c.name, list(args)))
        return 0


def app(name, **kwargs):
    return Component(name, "app", "local", "process", **kwargs)


def make(components, *, down=()):
    registry = Registry(1, {c.name: c for c in components})
    compose, process = Compose(), Process()
    def probe(c, env):
        ok = c.name not in down and (c.mode == "remote" or c.name in process.running or c.name in compose.started
                                    or (c.provisioning == "bundled" and c.owner in compose.started))
        return ProbeResult(ok, "reachable" if ok else "unreachable", "test probe")
    return Lifecycle(registry, compose=compose, process=process, probe_func=probe, output=lambda *a: None,
                     readiness_timeout=.1), compose, process


def test_embedded_preflight_environment_and_required_branch_failure_continue():
    db = Component("db", "app", "remote", "process")
    embedded = Component("inside", "app", "local", "embedded", owner="owner", requires={"db": Dependency("db", endpoint_env="DB_URL")})
    owner = app("owner", default_stack=True, requires={"inside": Dependency("inside")})
    other = app("other", default_stack=True)
    lifecycle, _, process = make([db, embedded, owner, other], down={"db"})
    assert lifecycle.start(None) != 0
    assert [(op, name) for op, name, _ in process.calls] == [("spawn", "other")]
    lifecycle, _, process = make([replace(db, endpoint="http://127.0.0.1:9"), embedded, owner])
    assert lifecycle.start(["owner"]) == 0
    assert process.calls[0][2]["DB_URL"] == "http://127.0.0.1:9"


def test_bundled_database_is_post_start_only_and_default_stop_deduplicates():
    bundle = Component("bundle", "middleware", "local", "bundled", owner="group", host="127.0.0.1", port=1,
                       host_env="BUNDLE_HOST", port_env="BUNDLE_PORT")
    group = Component("group", "app", "local", "compose", default_stack=True,
                      requires={"bundle": Dependency("bundle")})
    lifecycle, compose, _ = make([bundle, group])
    assert lifecycle.start(None) == 0
    assert compose.calls[0][:2] == ("up", "group")
    assert compose.calls[0][2]["BUNDLE_PORT"] == "1"
    assert lifecycle.stop(None) == 0
    assert [c[:2] for c in compose.calls] == [("up", "group"), ("down", "group")]


def test_remote_owner_is_opaque_and_explicit_stop_leaves_dependencies():
    dependency = app("dependency")
    embedded = Component("inside", "app", "local", "embedded", owner="owner",
                         requires={"dependency": Dependency("dependency")}, secret_env={"SECRET": "ABSENT"})
    owner = Component("owner", "app", "remote", "process", default_stack=True, requires={"inside": Dependency("inside")})
    lifecycle, compose, process = make([dependency, embedded, owner])
    assert lifecycle.start(None) == 0
    assert not compose.calls and not process.calls
    lifecycle, _, process = make([dependency, app("consumer", requires={"dependency": Dependency("dependency")})])
    assert lifecycle.stop(["consumer"]) == 0
    assert [c[:2] for c in process.calls] == [("stop", "consumer")]


def test_install_and_passive_operations_do_not_need_secrets():
    component = Component("image", "app", "local", "compose", default_stack=True,
                          secret_env={"DEST_PASSWORD": "SOURCE_PASSWORD"}, image_action="pull")
    lifecycle, compose, _ = make([component])
    assert lifecycle.install(None) == 0
    assert compose.calls[0][0] == "pull"
    assert compose.calls[0][2]["DEST_PASSWORD"] == "not-used-offline"
    compose.started.add("image")
    assert lifecycle.stop(["image"]) == 0
    assert lifecycle.logs("image", follow=False) == 0
    assert lifecycle.start(["image"]) != 0


def test_foreground_last_endpoint_transform_user_override_and_no_auto_start():
    server = app("server", endpoint="https://127.0.0.1:9/base/rpc")
    ui = Component("ui", "app", "local", "foreground", default_stack=True,
                   endpoint_args={"--server-url": "server"}, requires={"server": Dependency("server")})
    lifecycle, _, process = make([server, ui])
    assert lifecycle.start(None) == 0
    assert process.calls[-1] == ("foreground", "ui", ["--server-url", "wss://127.0.0.1:9/base/rpc"])
    lifecycle, _, process = make([server, ui], down={"server"})
    assert lifecycle.run_foreground(ui, ["--server-url=ws://127.0.0.1:1/rpc"]) == 0
    assert len(process.calls) == 1



def test_already_ready_external_compose_needs_no_local_launch_secrets():
    component = Component("external", "app", "local", "compose", default_stack=True,
                          secret_env={"PASSWORD": "UNAVAILABLE_PASSWORD"})
    lifecycle, compose, _ = make([component])
    lifecycle.probe = lambda c, env: ProbeResult(True, "reachable", "test external")
    assert lifecycle.start(None) == 0
    assert not compose.calls



def test_foreground_required_failure_is_blocked_even_if_old_endpoint_reachable():
    down = Component("down", "app", "remote", "none")
    server = app("server", requires={"down": Dependency("down")})
    ui = Component("ui", "app", "local", "foreground", default_stack=True,
                   requires={"server": Dependency("server")})
    lifecycle, _, process = make([down, server, ui], down={"down"})
    process.running.add("server")
    assert lifecycle.start(None) != 0
    assert not process.calls



def test_external_consumer_of_embedded_dependency_requires_live_owner():
    inside = Component("inside", "app", "local", "embedded", owner="owner")
    owner = app("owner", requires={"inside": Dependency("inside")})
    consumer = app("consumer", requires={"inside": Dependency("inside")})
    lifecycle, _, process = make([inside, owner, consumer])
    assert lifecycle.start(["consumer"]) != 0
    assert not process.calls
    process.running.add("owner")
    assert lifecycle.start(["consumer"]) == 0
    assert [call[:2] for call in process.calls] == [("spawn", "consumer")]


def test_external_consumer_of_remote_hosted_dependency_ignores_local_internals():
    database = app("database")
    inside = Component("inside", "app", "local", "embedded", owner="owner",
                       requires={"database": Dependency("database")})
    owner = Component("owner", "app", "remote", "none")
    consumer = app("consumer", requires={"inside": Dependency("inside")})
    lifecycle, _, process = make([database, inside, owner, consumer], down={"database"})
    assert lifecycle.start(["consumer"]) == 0
    assert [call[:2] for call in process.calls] == [("spawn", "consumer")]


def test_hosted_environment_conflicts_fail_before_launch():
    inside = Component("inside", "app", "local", "embedded", owner="owner", env={"SETTING": "two"})
    owner = app("owner", env={"SETTING": "one"}, requires={"inside": Dependency("inside")})
    lifecycle, _, process = make([inside, owner])
    assert lifecycle.start(["owner"]) != 0
    assert not process.calls


def test_status_reports_each_row_before_probing_the_next_component():
    remote_a = Component("a", "app", "remote", "none")
    remote_b = Component("b", "app", "remote", "none")
    lifecycle, _, _ = make([remote_a, remote_b])
    events = []

    def probe(component, environment):
        events.append(("probe", component.name))
        return ProbeResult(True, "reachable", "test probe")

    lifecycle.probe = probe
    rows = lifecycle.status(["a", "b"], on_row=lambda row: events.append(("row", row.component)))
    assert events == [("probe", "a"), ("row", "a"), ("probe", "b"), ("row", "b")]
    assert [row.component for row in rows] == ["a", "b"]


def test_start_reports_launch_and_readiness_progress():
    class SlowProcess(Process):
        def __init__(self):
            super().__init__()
            self.ready_checks = 0

        def status(self, component):
            if component.name not in self.running:
                return type("State", (), {"state": "stopped", "detail": ""})()
            self.ready_checks += 1
            state = "running" if self.ready_checks >= 8 else "starting"
            return type("State", (), {"state": state, "detail": "not ready"})()

    registry = Registry(1, {"worker": app("worker", default_stack=True)})
    process = SlowProcess()
    lines = []
    lifecycle = Lifecycle(registry, compose=Compose(), process=process,
                          probe_func=lambda component, environment: ProbeResult(True, "reachable", "test probe"),
                          output=lines.append, readiness_timeout=5, progress_interval=.05)
    assert lifecycle.start(["worker"]) == 0
    assert lines[:2] == ["worker: starting process (-)", "worker: waiting for readiness (-)"]
    assert lines[-1] == "worker: running (-)"
    assert any(line.startswith("worker: still waiting for readiness (") for line in lines)


def test_stopped_status_does_not_emit_an_empty_detail_segment():
    class Process:
        environ = {}

        def effective_component(self, component):
            return component

        def status(self, component):
            return type("State", (), {"state": "stopped", "detail": ""})()

    lifecycle = Lifecycle(Registry(1, {"worker": app("worker")}), compose=object(), process=Process())
    row = lifecycle.status(["worker"])[0]
    assert row.detail == "-"
    assert "; ;" not in row.detail
