"""End-to-end control tests; only test-owned loopback sockets and children."""
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import threading
from urllib.request import urlopen

import pytest

ROOT = Path(__file__).resolve().parents[2]
CLI = ROOT / "bin/icarus"
FIXTURE = "scripts/tests/fixtures/control_http.py"


def port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def control(tmp_path):
    config = tmp_path / "stack.toml"
    data = tmp_path / "data"
    def run(*args):
        return subprocess.run([str(CLI), "--config", str(config), *args], cwd=tmp_path,
                              env={**os.environ, "ICARUS_DATA_DIR": str(data)},
                              capture_output=True, text=True, timeout=25)
    yield config, data, run
    # Clean exact recorded test children even when an assertion fails.
    for record in (data / "runtime").rglob("*.json") if (data / "runtime").exists() else ():
        saved = json.loads(record.read_text())
        pid = saved["pid"]
        try:
            argv = Path(f"/proc/{pid}/cmdline").read_bytes()
            if b"control_http.py" in argv:
                os.killpg(pid, signal.SIGKILL) if saved.get("process_group") else os.kill(pid, signal.SIGKILL)
        except (ProcessLookupError, FileNotFoundError):
            pass


def write_stack(config, app_port, db_port, *, required=True, extra=""):
    config.write_text(f'''version = 1
[components.db]
kind = "middleware"
mode = "remote"
provisioning = "none"
host = "127.0.0.1"
port = {db_port}
probe = {{type = "tcp", timeout_seconds = 0.3}}
[components.child]
kind = "app"
provisioning = "process"
default_stack = true
endpoint = "http://127.0.0.1:{app_port}"
workdir = "."
start_command = ["{{python}}", "{{repo_root}}/{FIXTURE}", "--host", "{{host}}", "--port", "{{port}}", "--delay", "0.2"]
install_command = ["{{python}}", "-c", "print('installed fixture')"]
process_markers = ["{FIXTURE}"]
probe = {{type = "http", path = "/health", expect_body = {{status = "ready"}}}}
[components.child.env]
FIXTURE_VALUE = "from-registry"
[components.child.requires.db]
required = {str(required).lower()}
{extra}
''')


@pytest.fixture
def tcp_listener():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen()
    sock.settimeout(0.1)
    stop = threading.Event()
    def serve():
        while not stop.is_set():
            try:
                connection, _ = sock.accept()
                connection.close()
            except socket.timeout:
                pass
    worker = threading.Thread(target=serve)
    worker.start()
    yield sock.getsockname()[1]
    stop.set()
    worker.join()
    sock.close()


def test_actual_cli_install_start_ready_status_logs_idempotent_stop(control, tcp_listener):
    config, data, run = control
    app_port = port()
    write_stack(config, app_port, tcp_listener)
    env_before = (ROOT / ".env").read_bytes() if (ROOT / ".env").exists() else None
    result = run("install", "child")
    assert result.returncode == 0, result.stderr
    result = run("start", "child")
    assert result.returncode == 0, result.stdout + result.stderr
    with urlopen(f"http://127.0.0.1:{app_port}/health") as response:
        assert json.load(response)["value"] == "from-registry"
    record = data / "runtime/child.json"
    pid = json.loads(record.read_text())["pid"]
    assert run("start", "child").returncode == 0
    assert json.loads(record.read_text())["pid"] == pid
    result = run("status")
    assert result.returncode == 0 and "running" in result.stdout and "reachable" in result.stdout
    result = run("logs", "child", "--no-follow")
    assert result.returncode == 0 and "fixture started" in result.stdout
    result = run("stop", "child")
    assert result.returncode == 0, result.stdout + result.stderr
    assert not record.exists()
    with socket.socket() as sock:
        assert sock.connect_ex(("127.0.0.1", app_port)) != 0
    assert ((ROOT / ".env").read_bytes() if (ROOT / ".env").exists() else None) == env_before


def test_required_down_blocks_child_optional_warns_and_degrades(control):
    config, data, run = control
    app_port, db_port = port(), port()
    write_stack(config, app_port, db_port)
    result = run("start", "child")
    assert result.returncode != 0
    assert "child -> db" in result.stdout + result.stderr
    assert not (data / "runtime/child.json").exists()
    write_stack(config, app_port, db_port, required=False)
    result = run("start", "child")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "warning" in result.stdout.lower()
    assert "degraded" in run("status", "child").stdout


def test_ephemeral_port_override_recovers_fresh_status_stop(control, tcp_listener):
    config, data, run = control
    original, alternate = port(), port()
    write_stack(config, original, tcp_listener)
    before = config.read_bytes()
    result = run("start", "child", "--port", str(alternate))
    assert result.returncode == 0, result.stdout + result.stderr
    result = run("status", "child")
    assert "running" in result.stdout and str(alternate) in result.stdout
    assert run("stop", "child").returncode == 0
    assert config.read_bytes() == before


def test_external_service_and_remote_stop_are_never_signaled(control, tcp_listener):
    config, data, run = control
    app_port = port()
    write_stack(config, app_port, tcp_listener)
    child = subprocess.Popen([sys.executable, str(ROOT / FIXTURE), "--port", str(app_port)], cwd=config.parent,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        import time
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            with socket.socket() as sock:
                if sock.connect_ex(("127.0.0.1", app_port)) == 0:
                    break
            time.sleep(.05)
        result = run("start", "child")
        assert result.returncode == 0 and "external" in result.stdout
        assert not (data / "runtime/child.json").exists()
        assert run("stop", "child").returncode == 0
        assert run("stop", "db").returncode == 0
        assert child.poll() is None
    finally:
        child.terminate()
        child.wait()


def test_endpoint_override_uses_concrete_schema_valid_address_without_rewrite(control):
    config, data, run = control
    write_stack(config, port(), port(), required=False)
    original = config.read_bytes()
    # Remote stop is intentionally network-free and must accept a full URL override.
    result = run("stop", "child", "--mode", "remote", "--endpoint", "http://127.0.0.1:1")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "remote component not managed" in result.stdout
    assert config.read_bytes() == original
    assert not data.exists()


def test_config_cli_sources_strict_validation_no_secrets_or_docker(control):
    config, _, run = control
    write_stack(config, port(), port(), required=False)
    config.with_name("stack.local.toml").write_text('[components.child.env]\nFIXTURE_VALUE = "overlay"\n')
    result = run("config", "show", "child")
    assert result.returncode == 0, result.stderr
    doc = json.loads(result.stdout)
    assert doc["components"]["child"]["env"]["FIXTURE_VALUE"] == "overlay"
    assert str(config.with_name("stack.local.toml")) in doc["sources"]
    assert run("config", "validate").returncode == 0
    after = subprocess.run([str(CLI), "config", "validate", "--config", str(config)],
                           capture_output=True, text=True)
    assert after.returncode == 0, after.stderr
    assert run("start", "--mode", "remote").returncode != 0
    config.write_text(config.read_text() + '\n[components.db.requires.child]\nrequired = true\n')
    # Remote boundary deliberately opaque; local cycle must fail validation.
    config.write_text(config.read_text().replace('mode = "remote"', 'mode = "local"').replace('required = false', 'required = true'))
    assert run("config", "validate").returncode != 0



def test_production_webui_wrapper_uses_workdir_relative_identity(control):
    import shutil
    import sys
    sys.path.insert(0, str(ROOT / "scripts/icarus"))
    from registry import load_registry
    if not shutil.which("node") or not (ROOT / "apps/webui/apps/shell/dist/index.html").is_file():
        pytest.skip("native WebUI smoke requires existing Node and built dist")
    config, data, run = control
    webui = load_registry(ROOT).get("webui")
    app_port = port()
    config.write_text(f'''version = 1
[components.webui]
kind = "app"
provisioning = "process"
endpoint = "http://127.0.0.1:{app_port}"
workdir = "{webui.workdir}"
start_command = {json.dumps(list(webui.start_command))}
process_markers = {json.dumps(list(webui.process_markers))}
host_env = "ICARUS_WEBUI_HOST"
port_env = "ICARUS_WEBUI_PORT"
probe = {{type = "http", path = "/health"}}
''')
    try:
        result = run("start", "webui")
        record = json.loads((data / "runtime/webui.json").read_text())
        pid = record["pid"]
        proc = Path(f"/proc/{pid}")
        evidence = {"argv": (proc / "cmdline").read_bytes().replace(b"\0", b" ").decode() if proc.exists() else "exited",
                    "cwd": str((proc / "cwd").resolve()) if proc.exists() else "exited"}
        assert result.returncode == 0, result.stdout + result.stderr + repr(evidence)
        assert "running" in run("status", "webui").stdout
        assert run("stop", "webui").returncode == 0
    finally:
        path = data / "runtime/webui.json"
        if path.exists():
            pid = json.loads(path.read_text())["pid"]
            try:
                command = Path(f"/proc/{pid}/cmdline").read_bytes()
                if b"apps/webui/scripts/start.sh" in command:
                    os.killpg(pid, signal.SIGKILL)
            except (FileNotFoundError, ProcessLookupError):
                pass



def test_foreground_records_survive_new_cli_status_stop_and_override_target(control):
    import time
    config, data, run = control
    server_port, ui_port = port(), port()
    config.write_text(f'''version = 1
[components.server]
kind = "app"
mode = "remote"
provisioning = "none"
endpoint = "http://127.0.0.1:{server_port}"
probe = {{type = "http", path = "/health", timeout_seconds = 0.3}}
[components.ui]
kind = "app"
provisioning = "foreground"
workdir = "."
start_command = ["{{python}}", "{{repo_root}}/{FIXTURE}", "--port", "{ui_port}"]
process_markers = ["{FIXTURE}"]
endpoint_args = {{"--gateway-url" = "server"}}
[components.ui.requires.server]
required = true
''')
    result = run("tui")
    assert result.returncode != 0 and "not ready" in result.stderr
    assert not data.exists(), "opening UI must not auto-start dependencies"
    foreground = subprocess.Popen([str(CLI), "--config", str(config), "tui", "--gateway-url=ws://localhost:1/rpc"],
                                   cwd=ROOT, env={**os.environ, "ICARUS_DATA_DIR": str(data)},
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.monotonic() + 8
        ready = False
        while time.monotonic() < deadline:
            with socket.socket() as sock:
                if sock.connect_ex(("127.0.0.1", ui_port)) == 0:
                    ready = True
                    break
            if foreground.poll() is not None:
                break
            time.sleep(.05)
        assert ready, "explicit foreground target should bypass unavailable default server"
        assert "managed foreground process" in run("status", "ui").stdout
        result = run("stop", "ui")
        assert result.returncode == 0, result.stdout + result.stderr
        foreground.wait(timeout=10)
        assert not list((data / "runtime/ui").glob("*.json"))
    finally:
        if foreground.poll() is None:
            foreground.terminate()
            foreground.wait(timeout=5)


def test_config_path_selector_rejects_duplicate_and_missing_path(control):
    config, _, run = control
    write_stack(config, port(), port(), required=False)
    assert run("status", "--config", str(config)).returncode != 0
    result = subprocess.run([str(CLI), "--config"], capture_output=True, text=True)
    assert result.returncode != 0 and "requires a path" in result.stderr


def test_repository_default_configuration_validates_without_runtime():
    result = subprocess.run([str(CLI), "config", "validate"], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    result = subprocess.run([str(CLI), "config", "show"], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    components = json.loads(result.stdout)["components"]
    assert set(components) == {"gateway", "agent", "tui", "webui", "mem0", "openkb", "rela-mem", "postgres", "falkordb"}
    assert all(not c["default_stack"] for c in components.values() if c["kind"] == "middleware")
