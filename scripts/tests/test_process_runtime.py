from __future__ import annotations

from dataclasses import replace
import importlib
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time

import pytest

CONTROL_DIR = Path(__file__).resolve().parents[1] / "icarus"
if str(CONTROL_DIR) not in sys.path:
    sys.path.insert(0, str(CONTROL_DIR))
from registry import Component, Probe


@pytest.fixture
def api():
    try:
        module = importlib.import_module("process_runtime")
    except ModuleNotFoundError as error:
        if error.name != "process_runtime":
            raise
        module = None
    return module


def test_runtime_module_contract(api):
    assert api is not None, "ProcessRuntime must own real process lifecycle and identity"


@pytest.fixture
def listener(tmp_path, api):
    root = tmp_path / "repo"
    workdir = root / "app"
    workdir.mkdir(parents=True)
    # Deliberately outside repo: an explicit absolute marker is supported, but CWD still must match.
    script = tmp_path / "isolated_listener.py"
    script.write_text('''import http.server, json, os, sys
from pathlib import Path
Path("started.json").write_text(json.dumps({"cwd": os.getcwd(), "value": os.environ.get("CHILD_VALUE"), "args": sys.argv[3:]}))
print("listener-log-line", flush=True)
http.server.ThreadingHTTPServer((sys.argv[1], int(sys.argv[2])), http.server.SimpleHTTPRequestHandler).serve_forever()
''')
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    component = Component("fixture", "app", "local", "process", workdir="app",
                          endpoint=f"http://127.0.0.1:{port}",
                          start_command=("{python}", str(script), "{host}", "{port}"),
                          process_markers=(str(script),), probe=Probe("http"))
    runtime = api.ProcessRuntime(root, root / "data", dict(os.environ))
    processes = []
    yield runtime, component, script, processes
    # Tests own only exact recorded children/groups, never scan/kill by marker.
    for pid in processes:
        try:
            os.killpg(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            os.waitpid(pid, 0)
        except ChildProcessError:
            pass


def wait_for(check, seconds=6):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(.03)
    assert check(), "temporary process did not reach the expected state"


def start(listener, **kwargs):
    runtime, component, _, processes = listener
    pid = runtime.spawn(component, **kwargs)
    processes.append(pid)
    wait_for(lambda: runtime.status(component).state == "running")
    return pid


def test_real_http_spawn_status_idempotency_logs_and_stop(listener, capsys):
    runtime, component, _, _ = listener
    injected = {"CHILD_VALUE": "from-injection"}
    pid = start(listener, args=("literal;not-a-shell",), environment=injected)
    assert runtime.spawn(component) == pid
    assert injected == {"CHILD_VALUE": "from-injection"}
    actual = json.loads((runtime.repo_root / "app/started.json").read_text())
    assert actual == {"cwd": str(runtime.repo_root / "app"), "value": "from-injection",
                      "args": ["literal;not-a-shell"]}
    path = runtime.data_dir / "runtime/fixture.json"
    record = json.loads(path.read_text())
    assert record["pid"] == pid
    assert record["port"] == int(component.endpoint.rsplit(":", 1)[1])
    assert record["endpoint"] == component.endpoint
    assert record["workdir"] == str(runtime.repo_root / "app")
    assert record["process_started_at"]
    assert runtime.logs(component, follow=False) == 0
    assert "listener-log-line" in capsys.readouterr().out
    runtime.stop(component)
    assert runtime.status(component).state == "stopped"
    assert not path.exists()
    runtime.stop(component)
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


@pytest.mark.parametrize("changed", ["marker", "workdir", "starttime", "port", "mode"])
def test_changed_identity_refuses_stop_and_preserves_record(listener, changed, api):
    runtime, component, _, _ = listener
    pid = start(listener)
    path = runtime.data_dir / "runtime/fixture.json"
    original = path.read_text()
    if changed == "marker":
        target = replace(component, process_markers=("not-this-process",))
    elif changed == "workdir":
        target = replace(component, workdir=".")
    elif changed == "port":
        target = replace(component, endpoint="http://127.0.0.1:1")
    elif changed == "mode":
        target = replace(component, mode="remote")
    else:
        record = json.loads(original)
        record["process_started_at"] = "not-the-start-time"
        path.write_text(json.dumps(record))
        target = component
    retained = path.read_text()
    if changed == "mode":
        runtime.stop(target)
    else:
        with pytest.raises(api.ProcessError, match="identity"):
            runtime.stop(target)
    assert path.read_text() == retained
    os.kill(pid, 0)
    path.write_text(original)
    runtime.stop(component)


def test_orphan_requires_marker_cwd_and_configured_listener_port(listener):
    runtime, component, script, processes = listener
    port = component.endpoint.rsplit(":", 1)[1]
    process = subprocess.Popen([sys.executable, str(script), "127.0.0.1", port],
                               cwd=runtime.repo_root / "app", start_new_session=True,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    processes.append(process.pid)
    wait_for(lambda: runtime.status(component).state == "orphaned")
    assert runtime.status(replace(component, workdir=".")).state == "external"
    assert runtime.status(replace(component, endpoint="http://127.0.0.1:1")).state == "stopped"
    runtime.stop(component)
    process.wait(timeout=3)
    assert runtime.status(component).state == "stopped"


def test_foreign_marker_same_port_is_external_and_not_killed(listener):
    runtime, component, script, processes = listener
    port = component.endpoint.rsplit(":", 1)[1]
    process = subprocess.Popen([sys.executable, str(script), "127.0.0.1", port],
                               cwd=runtime.repo_root, start_new_session=True,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    processes.append(process.pid)
    wait_for(lambda: runtime.status(component).state == "external")
    runtime.stop(component)
    assert process.poll() is None


def test_listener_in_same_process_group_is_managed_and_cleaned(listener):
    runtime, component, script, processes = listener
    leader_script = script.parent / "leader.py"
    leader_script.write_text('''import subprocess, sys, time
subprocess.Popen([sys.executable, *sys.argv[1:]])
while True: time.sleep(1)
''')
    component = replace(component, process_markers=(str(leader_script),),
                        start_command=("{python}", str(leader_script), str(script), "{host}", "{port}"))
    pid = runtime.spawn(component)
    processes.append(pid)
    wait_for(lambda: runtime.status(component).state == "running")
    runtime.stop(component)
    wait_for(lambda: runtime.status(component).state == "stopped")
    with socket.socket() as sock:
        assert sock.connect_ex(("127.0.0.1", int(component.endpoint.rsplit(":", 1)[1]))) != 0


def test_foreground_keeps_caller_cwd_records_and_returns_exit_code(tmp_path, api):
    root = tmp_path / "repo"
    root.mkdir()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    script = root / "foreground.py"
    script.write_text('''import json, os, sys
from pathlib import Path
record = Path(os.environ["ICARUS_DATA_DIR"]) / "runtime/console" / (str(os.getpid()) + ".json")
import time
for _ in range(100):
    if record.exists(): break
    time.sleep(.01)
Path("observed.json").write_text(json.dumps({"record": json.loads(record.read_text()), "value": os.environ["FG_VALUE"]}))
sys.exit(7)
''')
    component = Component("console", "app", "local", "foreground",
                          start_command=("{python}", str(script)), process_markers=(str(script),))
    runtime = api.ProcessRuntime(root, root / "data", dict(os.environ))
    assert runtime.foreground(component, environment={"FG_VALUE": "injected"}, cwd=workspace) == 7
    observed = json.loads((workspace / "observed.json").read_text())
    assert observed["value"] == "injected"
    assert observed["record"]["workdir"] == str(workspace)
    assert observed["record"]["process_group"] is False
    assert list((root / "data/runtime/console").glob("*.json")) == []
    assert runtime.active_tui_records() == []


def test_foreground_records_are_discoverable_and_stopped_generically(tmp_path, api):
    from concurrent.futures import ThreadPoolExecutor
    script = tmp_path / "console.py"
    script.write_text("import time\nwhile True: time.sleep(.1)\n")
    component = Component("console", "app", "local", "foreground",
                          start_command=("{python}", str(script)), process_markers=(str(script),))
    runtime = api.ProcessRuntime(tmp_path, tmp_path / "data", dict(os.environ))
    with ThreadPoolExecutor() as executor:
        running = executor.submit(runtime.foreground, component)
        try:
            wait_for(lambda: bool(runtime.active_tui_records()))
            records = runtime.active_tui_records()
            assert len(records) == 1
            runtime.stop(component)
            wait_for(running.done, seconds=2)
            assert running.result() == -signal.SIGTERM
            assert runtime.active_tui_records() == []
        finally:
            for path in (runtime.data_dir / "runtime/console").glob("*.json"):
                pid = json.loads(path.read_text())["pid"]
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass


def test_probe_injection_and_no_data_directory_is_read_only(tmp_path, api):
    calls = []
    def readiness(component, environment):
        from probes import ProbeResult
        calls.append((component.name, environment))
        return ProbeResult(True, "reachable", "ready")
    runtime = api.ProcessRuntime(tmp_path, None, {}, probe_func=readiness)
    component = Component("absent", "app", "local", "process", endpoint="https://example.test")
    state = runtime.status(component)
    assert state is not None, "status must classify an independently ready endpoint"
    assert state.state == "external"
    runtime.stop(component)
    assert calls
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("argument", ["{unknown}", "{repo_root.__class__}", "{port!r}", "{port:04}"])
def test_rejects_unsupported_argv_placeholders_before_spawn(listener, api, argument):
    runtime, component, _, _ = listener
    with pytest.raises(api.ProcessError, match="placeholder"):
        runtime.spawn(replace(component, start_command=(argument,)))
    assert not (runtime.data_dir / "runtime/fixture.json").exists()


def test_marker_prefix_does_not_claim_a_foreign_listener(listener):
    runtime, component, script, processes = listener
    process = subprocess.Popen([sys.executable, str(script), "127.0.0.1", component.endpoint.rsplit(":", 1)[1]],
                               cwd=runtime.repo_root / "app", start_new_session=True,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    processes.append(process.pid)
    wrong = replace(component, process_markers=(str(script)[:-3],))
    wait_for(lambda: runtime.status(wrong).state == "external")
    runtime.stop(wrong)
    assert process.poll() is None


def test_logs_use_recorded_custom_path_and_endpoint_defaults(listener, capsys):
    runtime, component, _, processes = listener
    component = replace(component, start_command=("{python}", "-c", "import time; print('custom-log', flush=True); time.sleep(30)", "{host}", "{port}"),
                        process_markers=("import time; print('custom-log', flush=True); time.sleep(30)",),
                        endpoint="https://127.0.0.1")
    custom = runtime.data_dir / "logs/custom.log"
    pid = runtime.spawn(component, log_path=custom)
    processes.append(pid)
    wait_for(lambda: custom.exists() and "custom-log" in custom.read_text())
    assert runtime.logs(component, follow=False) == 0
    assert "custom-log" in capsys.readouterr().out
    record = json.loads((runtime.data_dir / "runtime/fixture.json").read_text())
    assert record["port"] == 443
    runtime.stop(component)


def test_stop_cleans_term_resistant_descendant_after_leader_exits(listener):
    runtime, component, script, processes = listener
    leader = script.parent / "resistant_group.py"
    leader.write_text('''import os, signal, time
from pathlib import Path
pid = os.fork()
if pid == 0:
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    Path("descendant.pid").write_text(str(os.getpid()))
    while True: time.sleep(.1)
while True: time.sleep(.1)
''')
    component = replace(component, endpoint=None, probe=None,
                        process_markers=(str(leader),), start_command=("{python}", str(leader)))
    pid = runtime.spawn(component)
    processes.append(pid)
    child_path = runtime.repo_root / "app/descendant.pid"
    wait_for(child_path.exists)
    descendant = int(child_path.read_text())
    runtime.stop(component)
    def descendant_dead():
        path = Path(f"/proc/{descendant}/stat")
        return not path.exists() or path.read_text().rsplit(")", 1)[1].split()[0] == "Z"
    assert descendant_dead(), "stop must escalate the captured descendant after its leader exits"
    assert not (runtime.data_dir / "runtime/fixture.json").exists()


def test_unverified_survivor_retains_snapshot_and_blocks_replacement(listener, api):
    runtime, component, script, processes = listener
    leader = script.parent / "changing_descendant.py"
    leader.write_text('''import os, signal, time
from pathlib import Path
pid = os.fork()
if pid == 0:
    signal.signal(signal.SIGTERM, lambda *_: os.chdir("/"))
    Path("changing.pid").write_text(str(os.getpid()))
    while True: time.sleep(.1)
while True: time.sleep(.1)
''')
    component = replace(component, endpoint=None, probe=None,
                        process_markers=(str(leader),), start_command=("{python}", str(leader)))
    processes.append(runtime.spawn(component))
    wait_for(lambda: (runtime.repo_root / "app/changing.pid").exists())
    with pytest.raises(api.ProcessError, match="record retained"):
        runtime.stop(component)
    record = json.loads((runtime.data_dir / "runtime/fixture.json").read_text())
    assert len(record["termination_members"]) == 2
    with pytest.raises(api.ProcessError, match="identity"):
        runtime.spawn(component)


def test_fresh_foreground_status_uses_validated_pid_records(tmp_path, api):
    from concurrent.futures import ThreadPoolExecutor
    script = tmp_path / "foreground_status.py"
    script.write_text("import time\nwhile True: time.sleep(.1)\n")
    component = Component("console", "app", "local", "foreground",
                          start_command=("{python}", str(script)), process_markers=(str(script),))
    owner = api.ProcessRuntime(tmp_path, tmp_path / "data", dict(os.environ))
    fresh = api.ProcessRuntime(tmp_path, owner.data_dir, dict(os.environ), {"console": (str(script),)})
    with ThreadPoolExecutor() as executor:
        running = executor.submit(owner.foreground, component)
        try:
            wait_for(lambda: bool(fresh.active_tui_records()))
            assert fresh.status(component).state == "running"
            assert fresh.status(replace(component, process_markers=("wrong-marker",))).state == "stopped"
            fresh.stop(component)
            wait_for(running.done)
            assert fresh.status(component).state == "stopped"
        finally:
            for path in (owner.data_dir / "runtime/console").glob("*.json"):
                try:
                    os.kill(json.loads(path.read_text())["pid"], signal.SIGKILL)
                except ProcessLookupError:
                    pass


def test_effective_component_recovers_override_across_instances(listener, api):
    runtime, launched, _, _ = listener
    start(listener)
    base = replace(launched, endpoint="http://127.0.0.1:1")
    fresh = api.ProcessRuntime(runtime.repo_root, runtime.data_dir, dict(os.environ))
    method = getattr(fresh, "effective_component", None)
    assert method is not None, "lifecycle needs an identity-checked saved launch address API"
    effective = method(base)
    assert effective.endpoint == launched.endpoint
    assert effective.port == int(launched.endpoint.rsplit(":", 1)[1])
    assert fresh.status(effective).state == "running"
    for changed in (replace(base, process_markers=("wrong",)), replace(base, workdir=".")):
        with pytest.raises(api.ProcessError, match="identity"):
            method(changed)
    path = runtime.data_dir / "runtime/fixture.json"
    original = path.read_text()
    record = json.loads(original)
    record["repo_root"] = str(runtime.repo_root.parent)
    path.write_text(json.dumps(record))
    with pytest.raises(api.ProcessError, match="identity"):
        method(base)
    path.write_text(original)
    fresh.stop(effective)
    assert fresh.status(effective).state == "stopped"



def test_effective_component_allows_same_config_restart_after_child_exit(listener, api):
    runtime, component, _, _ = listener
    pid = start(listener)
    # Simulate an application exiting before CLI cleanup, leaving its own record.
    os.killpg(pid, signal.SIGKILL)
    wait_for(lambda: not runtime._alive(pid))
    fresh = api.ProcessRuntime(runtime.repo_root, runtime.data_dir, dict(os.environ))
    effective = fresh.effective_component(component)
    assert effective == component
    new_pid = fresh.spawn(effective)
    try:
        wait_for(lambda: fresh.status(component).state == "running")
        assert new_pid != pid
    finally:
        fresh.stop(component)


@pytest.mark.parametrize("operation", ["effective_component", "stop", "spawn"])
def test_dead_leader_with_uncaptured_listener_refuses_and_retains_record(listener, api, operation):
    runtime, component, script, processes = listener
    leader = script.parent / "already_dead_leader.py"
    leader.write_text('''import subprocess, sys, time
from pathlib import Path
child = subprocess.Popen([sys.executable, *sys.argv[1:]], cwd="..")
Path("descendant.pid").write_text(str(child.pid))
while True: time.sleep(.1)
''')
    # The legitimate descendant need not share the leader's command or CWD.
    # No readiness probe may hide an unsafe replacement behind a reachable port.
    component = replace(component, probe=None, process_markers=(str(leader),),
                        start_command=("{python}", str(leader), str(script), "{host}", "{port}"))
    pid = runtime.spawn(component)
    processes.append(pid)
    child_path = runtime.repo_root / "app/descendant.pid"
    wait_for(child_path.exists)
    descendant = int(child_path.read_text())
    port = int(component.endpoint.rsplit(":", 1)[1])
    wait_for(lambda: runtime._listeners(pid, port, True))
    child_identity = runtime._member_snapshot(descendant)
    assert child_identity["group"] == pid
    assert child_identity["cwd"] == str(runtime.repo_root)
    assert not runtime._command_matches(descendant, component.process_markers, runtime.repo_root / "app")
    path = runtime.data_dir / "runtime/fixture.json"
    original = path.read_bytes()
    assert "termination_members" not in json.loads(original)
    # Kill and reap ONLY the leader, before a fresh runtime observes the record.
    child = runtime._children[pid]
    child.kill()
    child.wait(timeout=3)
    assert not runtime._alive(pid)
    assert runtime._alive(descendant)
    fresh = api.ProcessRuntime(runtime.repo_root, runtime.data_dir, dict(os.environ))
    with pytest.raises(api.ProcessError, match="group.*record retained"):
        result = getattr(fresh, operation)(component)
        if operation == "spawn":
            processes.append(result)  # Own cleanup even when the unfixed spawn succeeds.
    assert path.read_bytes() == original
    assert fresh._member_matches(child_identity)
    assert fresh._alive(descendant), "an uncaptured survivor must never be signaled or adopted"
    with socket.create_connection(("127.0.0.1", port), timeout=1):
        pass
    with pytest.raises(api.ProcessError, match="group.*record retained"):
        fresh.spawn(component)
    assert path.read_bytes() == original


def test_missing_command_and_workdir_errors_do_not_spawn(listener, api):
    runtime, component, _, _ = listener
    for target in (replace(component, start_command=()), replace(component, workdir="missing")):
        with pytest.raises(api.ProcessError):
            runtime.spawn(target)
    assert not (runtime.data_dir / "runtime/fixture.json").exists()
