#!/usr/bin/env python3
"""Repository-level installer and lifecycle control for Icarus apps."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import time
from typing import Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

from environment import expand_braced_variables, read_env_file


REPO_ROOT = Path(__file__).resolve().parents[2]
BACKGROUND_PROJECTS = ("mem0", "openkb", "gateway")
KNOWN_PROJECTS = ("agent", *BACKGROUND_PROJECTS, "tui")
STATUS_ORDER = ("mem0", "openkb", "gateway", "agent", "tui")
COMPOSE_PROJECTS = {"mem0": "mem0-dev", "openkb": "openkb-dev"}
COMPOSE_FILES = {
    "mem0": REPO_ROOT / "apps/mem0/server/docker-compose.yaml",
    "openkb": REPO_ROOT / "apps/openkb/docker-compose.yaml",
}
EXPECTED_CONTAINERS = {"mem0": 3, "openkb": 1}
ENDPOINTS = {
    "mem0": "http://127.0.0.1:8888/docs",
    "openkb": "http://127.0.0.1:7566/openapi.json",
    "gateway": "http://127.0.0.1:8765/health",
}
PORTS = {"mem0": 8888, "openkb": 7566, "gateway": 8765}
PROCESS_MARKERS = {
    "gateway": ("apps.gateway.src.main", "apps/gateway/scripts/start.sh"),
    "tui": ("apps.tui.src.main", "apps/tui/scripts/start.sh"),
}


class ControlError(RuntimeError):
    """A user-facing lifecycle error."""


@dataclass(frozen=True)
class ProjectStatus:
    project: str
    state: str
    detail: str = "-"


class IcarusControl:
    def __init__(
        self,
        repo_root: Path = REPO_ROOT,
        *,
        cwd: Path | None = None,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        self.repo_root = repo_root.resolve()
        self.cwd = (cwd or Path.cwd()).resolve()
        self.environ = dict(os.environ if environ is None else environ)

    def run(self, argv: Sequence[str]) -> int:
        args = list(argv)
        if not args:
            return self.run_tui([])
        if args[0] in {"-h", "--help", "help"}:
            self.print_help()
            return 0
        if args[0].startswith("-"):
            return self.run_tui(args)

        command = args.pop(0)
        if args and args[0] in {"-h", "--help"}:
            self.print_help()
            return 0
        if command == "install":
            return self.install(args)
        if command == "start":
            return self.start(args)
        if command == "tui":
            return self.run_tui(args)
        if command == "stop":
            return self.stop(args)
        if command == "status":
            return self.show_status(args)
        raise ControlError(
            f"unknown command: {command}. Run 'icarus help' for usage."
        )

    def print_help(self) -> None:
        print(
            """Usage:
  icarus install [agent|gateway|tui|mem0|openkb] [--dev]
  icarus start [mem0|openkb|gateway|tui] [TUI options]
  icarus tui [--session-id ID] [--gateway-url URL]
  icarus stop [mem0|openkb|gateway|tui]
  icarus status [agent|mem0|openkb|gateway|tui]

With no project, start and stop operate on the complete Icarus stack.
Agent runs inside Gateway and has no independent process.
Legacy 'icarus --session-id ID' still opens the TUI."""
        )

    def install(self, args: Sequence[str]) -> int:
        project, dev = self._parse_install_args(args)
        self._ensure_env_template()
        if project is None:
            self._check_docker()
            command = [str(self.repo_root / "scripts/install.sh")]
            if dev:
                command.append("--dev")
            self._run_checked(command)
            self._install_service("mem0")
            self._install_service("openkb")
            self._run_checked(
                [str(self.repo_root / "scripts/install-commands.sh")]
            )
            return 0

        if project in {"agent", "gateway", "tui"}:
            command = [
                str(self.repo_root / f"apps/{project}/scripts/install.sh")
            ]
            if dev:
                command.append("--dev")
            self._run_checked(command)
        else:
            if dev:
                raise ControlError(f"--dev does not apply to {project}")
            self._check_docker()
            self._install_service(project)
        self._run_checked([str(self.repo_root / "scripts/install-commands.sh")])
        return 0

    def _install_service(self, project: str) -> None:
        script = self.repo_root / f"apps/{project}/scripts/icarus-compose.sh"
        print(f"{project}: building app-owned Docker image")
        self._run_checked(["bash", str(script), "build"])

    def start(self, args: Sequence[str]) -> int:
        project, remaining = self._parse_project_args(args, allow_tui_args=True)
        if project == "agent":
            raise ControlError(
                "agent runs inside gateway; use 'icarus start gateway'"
            )
        if project == "tui":
            return self.run_tui(remaining)
        if project is not None:
            if remaining:
                raise ControlError(f"{project} does not accept arguments")
            self.start_background(project)
            return 0

        for name in BACKGROUND_PROJECTS:
            self.start_background(name)
        return self.run_tui(remaining)

    def start_background(self, project: str) -> None:
        status = self.project_status(project)
        if status.state == "running":
            print(f"{project}: already running ({status.detail})")
            return
        if status.state == "external":
            print(f"{project}: already available externally ({status.detail})")
            return
        if status.state == "starting":
            print(f"{project}: waiting for existing startup")
            self._wait_until_healthy(project)
            print(f"{project}: running ({ENDPOINTS[project]})")
            return
        if status.state == "unhealthy":
            raise ControlError(
                f"{project} is running but unhealthy ({status.detail}); "
                f"run 'icarus stop {project}' before restarting it"
            )
        if self._port_open(PORTS[project]) and not self._healthy(project):
            raise ControlError(
                f"{project} cannot start: port {PORTS[project]} is already in use "
                "by an unrecognized service"
            )

        if project in COMPOSE_PROJECTS:
            self._require_data_dir()
            script = self.repo_root / f"apps/{project}/scripts/icarus-compose.sh"
            print(f"{project}: starting")
            self._run_checked(["bash", str(script), "up", "-d", "--build"])
        elif project == "gateway":
            self._start_gateway()
        else:
            raise ControlError(f"unknown background project: {project}")

        self._wait_until_healthy(project)
        print(f"{project}: running ({ENDPOINTS[project]})")

    def run_tui(self, args: Sequence[str]) -> int:
        if self._uses_default_gateway(args) and not self._healthy("gateway"):
            raise ControlError(
                "gateway is not ready; run 'icarus start gateway' or "
                "'icarus start' first"
            )
        script = self.repo_root / "apps/tui/scripts/start.sh"
        if not (self.repo_root / "apps/tui/.venv/bin/python").is_file():
            raise ControlError("TUI environment is missing; run 'icarus install'")

        process = subprocess.Popen(
            [str(script), *args],
            cwd=self.cwd,
            env=self.environ,
        )
        record_path = self._tui_record_path(process.pid)
        self._write_process_record(
            record_path, process.pid, "tui", process_group=False,
            extra={"workspace": str(self.cwd)},
        )
        try:
            return process.wait()
        except KeyboardInterrupt:
            try:
                return process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.terminate()
                return process.wait()
        finally:
            self._remove_record_if_pid(record_path, process.pid)

    @staticmethod
    def _uses_default_gateway(args: Sequence[str]) -> bool:
        for index, value in enumerate(args):
            if value == "--gateway-url":
                return (
                    index + 1 >= len(args)
                    or args[index + 1] == "ws://127.0.0.1:8765/rpc"
                )
            if value.startswith("--gateway-url="):
                return value.split("=", 1)[1] == "ws://127.0.0.1:8765/rpc"
        return True

    def stop(self, args: Sequence[str]) -> int:
        project, remaining = self._parse_project_args(args)
        if remaining:
            raise ControlError("stop does not accept extra arguments")
        if project == "agent":
            raise ControlError(
                "agent runs inside gateway; use 'icarus stop gateway'"
            )
        if project is not None:
            self.stop_project(project)
            return 0
        for name in ("tui", "gateway", "openkb", "mem0"):
            self.stop_project(name)
        return 0

    def stop_project(self, project: str) -> None:
        if project == "tui":
            self._stop_tui_processes()
            return
        if project == "gateway":
            self._stop_gateway()
            return

        containers = self._compose_containers(project)
        if not containers:
            if self._healthy(project):
                print(f"{project}: external service left running")
            else:
                print(f"{project}: already stopped")
            return
        script = self.repo_root / f"apps/{project}/scripts/icarus-compose.sh"
        self._run_checked(["bash", str(script), "down"])
        print(f"{project}: stopped (data preserved)")

    def show_status(self, args: Sequence[str]) -> int:
        project, remaining = self._parse_project_args(
            args, allowed=KNOWN_PROJECTS
        )
        if remaining:
            raise ControlError("status does not accept extra arguments")
        names = (project,) if project else STATUS_ORDER
        rows = [self.project_status(name) for name in names]
        widths = {
            "project": max(len("PROJECT"), *(len(row.project) for row in rows)),
            "state": max(len("STATUS"), *(len(row.state) for row in rows)),
        }
        print(
            f"{'PROJECT':<{widths['project']}}  "
            f"{'STATUS':<{widths['state']}}  DETAIL"
        )
        for row in rows:
            print(
                f"{row.project:<{widths['project']}}  "
                f"{row.state:<{widths['state']}}  {row.detail}"
            )
        return 0

    def project_status(self, project: str) -> ProjectStatus:
        if project == "agent":
            gateway = self.project_status("gateway")
            detail = "embedded in gateway"
            if gateway.state in {"running", "external"}:
                return ProjectStatus(project, gateway.state, detail)
            return ProjectStatus(project, "stopped", detail)
        if project == "tui":
            records = self._active_tui_records()
            if records:
                return ProjectStatus(
                    project, "running", f"{len(records)} managed process(es)"
                )
            return ProjectStatus(project, "stopped")
        if project == "gateway":
            record = self._read_record(self._gateway_record_path())
            managed = record is not None and self._record_matches(
                record, "gateway"
            )
            healthy = self._healthy(project)
            if managed and healthy:
                return ProjectStatus(project, "running", ENDPOINTS[project])
            if managed:
                age = time.time() - float(record.get("created_at", 0))
                state = "starting" if age < 30 else "unhealthy"
                return ProjectStatus(project, state, self._gateway_log_detail())
            if healthy:
                return ProjectStatus(project, "external", ENDPOINTS[project])
            return ProjectStatus(project, "stopped")

        containers = self._compose_containers(project)
        healthy = self._healthy(project)
        running = [row for row in containers if row.get("State") == "running"]
        health_states = {str(row.get("HealthStatus", "none")) for row in running}
        complete = len(containers) >= EXPECTED_CONTAINERS[project]
        if (
            complete
            and len(running) == len(containers)
            and not health_states.intersection({"starting", "unhealthy"})
            and healthy
        ):
            return ProjectStatus(project, "running", ENDPOINTS[project])
        if not running:
            if healthy:
                return ProjectStatus(project, "external", ENDPOINTS[project])
            return ProjectStatus(project, "stopped")
        if containers:
            text = " ".join(str(row.get("Status", "")) for row in running)
            state = (
                "starting"
                if "starting" in text.lower() or not complete
                else "unhealthy"
            )
            detail = text or f"{len(running)}/{len(containers)} containers running"
            return ProjectStatus(project, state, detail)
        if healthy:
            return ProjectStatus(project, "external", ENDPOINTS[project])
        return ProjectStatus(project, "stopped")

    def _start_gateway(self) -> None:
        python = self.repo_root / "apps/gateway/.venv/bin/python"
        if not python.is_file():
            raise ControlError("Gateway environment is missing; run 'icarus install'")
        self._require_data_dir()
        log_path = self._logs_dir() / "gateway.log"
        log_handle = open(log_path, "ab", buffering=0)
        try:
            process = subprocess.Popen(
                [str(self.repo_root / "apps/gateway/scripts/start.sh")],
                cwd=self.repo_root,
                env=self.environ,
                stdin=subprocess.DEVNULL,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        finally:
            log_handle.close()
        self._write_process_record(
            self._gateway_record_path(), process.pid, "gateway",
            process_group=True, extra={"log": str(log_path)},
        )
        print(f"gateway: starting (log: {log_path})")

    def _stop_gateway(self) -> None:
        path = self._gateway_record_path()
        record = self._read_record(path)
        if record is None or not self._record_matches(record, "gateway"):
            if record is not None:
                path.unlink(missing_ok=True)
            if self._healthy("gateway"):
                print("gateway: external service left running")
            else:
                print("gateway: already stopped")
            return
        self._terminate_record(record)
        path.unlink(missing_ok=True)
        print("gateway: stopped")

    def _stop_tui_processes(self) -> None:
        records = self._active_tui_records()
        if not records:
            print("tui: already stopped")
            return
        for path, record in records:
            self._terminate_record(record)
            path.unlink(missing_ok=True)
        print(f"tui: stopped {len(records)} managed process(es)")

    def _wait_until_healthy(self, project: str, timeout: float = 180) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            ready = (
                self.project_status(project).state == "running"
                if project in COMPOSE_PROJECTS
                else self._healthy(project)
            )
            if ready:
                return
            if project == "gateway":
                record = self._read_record(self._gateway_record_path())
                if record is None or not self._record_matches(record, "gateway"):
                    raise ControlError(
                        f"gateway exited before becoming ready; {self._gateway_log_detail()}"
                    )
            time.sleep(0.25)
        raise ControlError(
            f"{project} did not become ready within {int(timeout)} seconds; "
            f"check 'icarus status {project}'"
        )

    def _healthy(self, project: str) -> bool:
        try:
            with urlopen(ENDPOINTS[project], timeout=0.5) as response:
                if response.status < 200 or response.status >= 400:
                    return False
                if project == "gateway":
                    value = json.loads(response.read().decode("utf-8"))
                    return value == {"status": "ready"}
                return True
        except (HTTPError, URLError, TimeoutError, ValueError, OSError):
            return False

    def _port_open(self, port: int) -> bool:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return True
        except OSError:
            return False

    def _compose_containers(self, project: str) -> list[dict[str, object]]:
        docker = shutil.which("docker")
        if docker is None:
            return []
        try:
            completed = subprocess.run(
                [
                    docker, "ps", "-a", "--filter",
                    f"label=com.docker.compose.project={COMPOSE_PROJECTS[project]}",
                    "--filter",
                    f"label=com.docker.compose.project.config_files={COMPOSE_FILES[project]}",
                    "--format", "{{json .}}",
                ],
                check=False, capture_output=True, text=True, timeout=3,
            )
        except (OSError, subprocess.TimeoutExpired):
            return []
        if completed.returncode != 0:
            return []
        rows = []
        for line in completed.stdout.splitlines():
            try:
                value = json.loads(line)
            except ValueError:
                continue
            if isinstance(value, dict):
                rows.append(value)
        return rows

    def _check_docker(self) -> None:
        docker = shutil.which("docker")
        standalone = shutil.which("docker-compose")
        if docker is None and standalone is None:
            raise ControlError(
                "Docker with Compose is required for Mem0 and OpenKB"
            )
        if standalone is not None:
            return
        completed = subprocess.run(
            [str(docker), "compose", "version"], check=False,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        if completed.returncode != 0:
            raise ControlError(
                "Docker Compose plugin or docker-compose is required for Mem0 and OpenKB"
            )

    def _require_data_dir(self) -> Path:
        path = self._optional_data_dir()
        if path is None:
            raise ControlError(
                "ICARUS_DATA_DIR must be an absolute path in the repository .env"
            )
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _optional_data_dir(self) -> Path | None:
        value = self.environ.get("ICARUS_DATA_DIR")
        if not value:
            value = self._dotenv_values().get("ICARUS_DATA_DIR")
        if not value:
            return None
        values = {**self._dotenv_values(), **self.environ}
        value = expand_braced_variables(value, values)
        path = Path(value).expanduser()
        if not path.is_absolute():
            return None
        return path.resolve()

    def _dotenv_values(self) -> dict[str, str]:
        return read_env_file(self.repo_root / ".env")

    def _runtime_dir(self) -> Path:
        path = self._require_data_dir() / "runtime"
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        return path

    def _logs_dir(self) -> Path:
        path = self._require_data_dir() / "logs"
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        return path

    def _gateway_record_path(self) -> Path:
        data_dir = self._optional_data_dir()
        if data_dir is None:
            return self.repo_root / ".missing-icarus-data-dir/gateway.json"
        return data_dir / "runtime/gateway.json"

    def _tui_record_path(self, pid: int) -> Path:
        path = self._runtime_dir() / "tui"
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        return path / f"{pid}.json"

    def _write_process_record(
        self, path: Path, pid: int, project: str, *, process_group: bool,
        extra: Mapping[str, object] | None = None,
    ) -> None:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        record: dict[str, object] = {
            "pid": pid,
            "project": project,
            "marker": PROCESS_MARKERS[project][0],
            "process_group": process_group,
            "created_at": time.time(),
            "process_started_at": self._process_started_at(pid),
        }
        record.update(extra or {})
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(record), encoding="utf-8")
        os.chmod(temporary, 0o600)
        temporary.replace(path)

    def _read_record(self, path: Path) -> dict[str, object] | None:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return value if isinstance(value, dict) else None

    def _record_matches(self, record: Mapping[str, object], project: str) -> bool:
        pid = record.get("pid")
        if not isinstance(pid, int) or pid <= 0:
            return False
        command = self._process_command(pid)
        if not command or not any(
            marker in command for marker in PROCESS_MARKERS[project]
        ):
            return False
        recorded_start = record.get("process_started_at")
        current_start = self._process_started_at(pid)
        return not recorded_start or recorded_start == current_start

    def _active_tui_records(self) -> list[tuple[Path, dict[str, object]]]:
        data_dir = self._optional_data_dir()
        if data_dir is None:
            return []
        directory = data_dir / "runtime/tui"
        if not directory.is_dir():
            return []
        active = []
        for path in directory.glob("*.json"):
            record = self._read_record(path)
            if record is not None and self._record_matches(record, "tui"):
                active.append((path, record))
            else:
                path.unlink(missing_ok=True)
        return active

    def _process_command(self, pid: int) -> str:
        completed = subprocess.run(
            ["ps", "-ww", "-p", str(pid), "-o", "command="],
            check=False, capture_output=True, text=True,
        )
        return completed.stdout.strip() if completed.returncode == 0 else ""

    def _process_started_at(self, pid: int) -> str:
        completed = subprocess.run(
            ["ps", "-ww", "-p", str(pid), "-o", "lstart="],
            check=False, capture_output=True, text=True,
        )
        return completed.stdout.strip() if completed.returncode == 0 else ""

    def _terminate_record(self, record: Mapping[str, object]) -> None:
        pid = int(record["pid"])
        try:
            if record.get("process_group"):
                os.killpg(pid, signal.SIGTERM)
            else:
                os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if not self._process_command(pid):
                return
            time.sleep(0.1)
        if not self._record_matches(record, str(record["project"])):
            return
        try:
            if record.get("process_group"):
                os.killpg(pid, signal.SIGKILL)
            else:
                os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    def _remove_record_if_pid(self, path: Path, pid: int) -> None:
        record = self._read_record(path)
        if record is not None and record.get("pid") == pid:
            path.unlink(missing_ok=True)

    def _gateway_log_detail(self) -> str:
        record = self._read_record(self._gateway_record_path())
        if record and isinstance(record.get("log"), str):
            return f"log: {record['log']}"
        data_dir = self._optional_data_dir()
        return str(data_dir / "logs/gateway.log") if data_dir else "-"

    def _ensure_env_template(self) -> None:
        target = self.repo_root / ".env"
        if target.exists():
            return
        source = self.repo_root / ".example.env"
        shutil.copyfile(source, target)
        os.chmod(target, 0o600)
        print(f"Created {target} from .example.env; fill it before starting Icarus.")

    def _parse_install_args(
        self, args: Sequence[str]
    ) -> tuple[str | None, bool]:
        dev = False
        projects = []
        for value in args:
            if value == "--dev":
                dev = True
            elif value.startswith("-"):
                raise ControlError(f"unknown install option: {value}")
            else:
                projects.append(value)
        if len(projects) > 1:
            raise ControlError("install accepts at most one project")
        project = projects[0] if projects else None
        if project is not None and project not in KNOWN_PROJECTS:
            raise ControlError(f"unknown project: {project}")
        return project, dev

    def _parse_project_args(
        self, args: Sequence[str], *, allow_tui_args: bool = False,
        allowed: Sequence[str] = KNOWN_PROJECTS,
    ) -> tuple[str | None, list[str]]:
        values = list(args)
        project = None
        if values and values[0] in allowed:
            project = values.pop(0)
        elif values and not (allow_tui_args and values[0].startswith("-")):
            raise ControlError(f"unknown project: {values[0]}")
        return project, values

    def _run_checked(self, command: Sequence[str]) -> None:
        try:
            subprocess.run(
                list(command), cwd=self.cwd, env=self.environ, check=True
            )
        except FileNotFoundError as error:
            raise ControlError(f"command not found: {command[0]}") from error
        except subprocess.CalledProcessError as error:
            raise ControlError(
                f"command failed with exit code {error.returncode}: {command[0]}"
            ) from error


def main(argv: Sequence[str] | None = None) -> int:
    control = IcarusControl()
    try:
        return control.run(sys.argv[1:] if argv is None else argv)
    except ControlError as error:
        print(f"icarus: {error}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
