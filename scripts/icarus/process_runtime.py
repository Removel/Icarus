"""Local process operations with PID, command, CWD, start and listener identity."""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
import json
import os
from pathlib import Path
import signal
import string
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit

from probes import ProbeResult, probe
from registry import Component


class ProcessError(RuntimeError):
    """Unsafe identity or an unsuccessful local process operation."""


@dataclass(frozen=True)
class ProcessState:
    state: str
    detail: str = ""


class ProcessRuntime:
    def __init__(
        self, repo_root: Path, data_dir: Path | None, environ: Mapping[str, str],
        markers: Mapping[str, tuple[str, ...]] | None = None, *,
        probe_func: Callable[[Component, Mapping[str, str]], ProbeResult] = probe,
    ):
        self.repo_root = Path(repo_root).resolve()
        self.data_dir = Path(data_dir).resolve() if data_dir is not None else None
        self.environ = dict(environ)
        self.markers = dict(markers or {})
        self.probe_func = probe_func
        self._children: dict[int, subprocess.Popen] = {}
        self._components: dict[str, Component] = {}

    def _markers(self, component: Component) -> tuple[str, ...]:
        return component.process_markers or self.markers.get(component.name, ())

    def _address(self, component: Component) -> tuple[str | None, int | None]:
        endpoint = urlsplit(component.endpoint or "")
        return (component.host or endpoint.hostname,
                component.port or endpoint.port or
                ({"http": 80, "https": 443, "ws": 80, "wss": 443}.get(endpoint.scheme)))

    def _workdir(self, component: Component) -> Path:
        path = (self.repo_root / (component.workdir or ".")).resolve()
        if not path.is_relative_to(self.repo_root):
            raise ProcessError(f"{component.name}: workdir escapes repository")
        return path

    def _argv(self, component: Component, args: Sequence[str]) -> list[str]:
        host, port = self._address(component)
        values = {"repo_root": str(self.repo_root), "python": sys.executable,
                  "data_dir": str(self.data_dir) if self.data_dir else None,
                  "host": component.bind_host or host, "port": str(port) if port else None}
        result = []
        try:
            for argument in (*component.start_command, *args):
                for _, field, spec, conversion in string.Formatter().parse(argument):
                    if field is not None and (field not in values or values[field] is None or spec or conversion):
                        raise ValueError
                result.append(argument.format_map(values))
        except (ValueError, KeyError, AttributeError):
            raise ProcessError(f"{component.name}: unsupported or unavailable argv placeholder") from None
        if not component.start_command:
            raise ProcessError(f"{component.name}: start_command is required")
        return result

    def _environment(self, injected=None) -> dict[str, str]:
        environment = {**self.environ, **(injected or {})}
        if self.data_dir is not None:
            environment["ICARUS_DATA_DIR"] = str(self.data_dir)
        return environment

    def _path(self, component: Component) -> Path | None:
        if Path(component.name).name != component.name or component.name in {".", ".."}:
            raise ProcessError("invalid component record name")
        if self.data_dir is None:
            return None
        path = self.data_dir / "runtime" / f"{component.name}.json"
        if not path.resolve().is_relative_to(self.data_dir):
            raise ProcessError(f"{component.name}: record path escapes ICARUS_DATA_DIR")
        return path

    @staticmethod
    def _read(path: Path | None) -> dict | None:
        try:
            value = json.loads(path.read_text()) if path else None
            return value if isinstance(value, dict) else None
        except (OSError, ValueError, UnicodeError):
            return None

    def _record(self, component: Component, pid: int, cwd: Path, group: bool, log_path=None) -> dict:
        host, port = self._address(component)
        return {"pid": pid, "project": component.name, "repo_root": str(self.repo_root),
                "markers": list(self._markers(component)), "workdir": str(cwd),
                "process_started_at": self._started(pid), "created_at": time.time(),
                "process_group": group, "endpoint": component.endpoint,
                "host": host, "port": port, "log": str(log_path) if log_path else None,
                "foreground": component.provisioning == "foreground"}

    def _write(self, path: Path, record: dict) -> None:
        if self.data_dir is None or not path.resolve().is_relative_to(self.data_dir):
            raise ProcessError("record path escapes ICARUS_DATA_DIR")
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".record-")
        try:
            with os.fdopen(fd, "w") as output:
                json.dump(record, output)
            os.replace(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)

    def _remove(self, path: Path, record: dict) -> None:
        if path.resolve().is_relative_to(self.data_dir) and self._read(path) == record:
            path.unlink(missing_ok=True)

    @staticmethod
    def _stat(pid: int) -> tuple[str, int, str] | None:
        try:
            # Fields after the final ')' are state, ppid, pgrp, ... starttime.
            fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
            return fields[0], int(fields[2]), fields[19]
        except (OSError, ValueError, IndexError):
            return None

    def _alive(self, pid: int) -> bool:
        child = self._children.get(pid)
        if child is not None and child.poll() is not None:
            self._children.pop(pid, None)
            return False
        state = self._stat(pid)
        return state is not None and state[0] != "Z"

    def _started(self, pid: int) -> str:
        state = self._stat(pid)
        return f"proc:{state[2]}" if state else ""

    @staticmethod
    def _same_start(recorded, current) -> bool:
        if not recorded or not current:
            return False
        if recorded == current:
            return True
        # Accept legacy ps lstart timestamps within the original two-second tolerance.
        try:
            return abs((datetime.strptime(recorded, "%a %b %d %H:%M:%S %Y") -
                        datetime.strptime(current, "%a %b %d %H:%M:%S %Y")).total_seconds()) <= 2
        except (TypeError, ValueError):
            return False

    def _command_matches(self, pid: int, markers: Sequence[str], cwd: Path) -> bool:
        try:
            if Path(f"/proc/{pid}/cwd").resolve(strict=True) != cwd:
                return False
            tokens = Path(f"/proc/{pid}/cmdline").read_bytes().decode(errors="replace").split("\0")
        except OSError:
            return False
        for marker in markers:
            target = (cwd / marker).resolve()
            for token in tokens:
                if token == marker:
                    return True
                if token and ("/" in marker or "/" in token):
                    try:
                        if (cwd / token).resolve() == target:
                            return True
                    except (OSError, ValueError):
                        continue
        return False

    def _metadata_matches(self, record: dict, component: Component, cwd: Path | None = None) -> bool:
        host, port = self._address(component)
        return (record.get("project") == component.name and record.get("repo_root") == str(self.repo_root)
                and record.get("markers") == list(self._markers(component))
                and record.get("workdir") == str(cwd or self._workdir(component))
                and record.get("endpoint") == component.endpoint
                and record.get("host") == host and record.get("port") == port)

    def _matches(self, record: dict, component: Component, cwd: Path | None = None) -> bool:
        pid = record.get("pid")
        return (type(pid) is int and pid > 0 and self._metadata_matches(record, component, cwd)
                and self._alive(pid) and self._same_start(record.get("process_started_at"), self._started(pid))
                and self._command_matches(pid, self._markers(component), cwd or self._workdir(component)))

    def _listeners(self, pid: int, port: int, group: bool) -> bool:
        owners = [pid]
        if group and (state := self._stat(pid)) and state[1] == pid:
            owners = [int(p.name) for p in Path("/proc").iterdir() if p.name.isdigit()
                      and (info := self._stat(int(p.name))) and info[1] == pid and info[0] != "Z"]
        sockets = set()
        for owner in owners:
            try:
                for fd in Path(f"/proc/{owner}/fd").iterdir():
                    try:
                        target = os.readlink(fd)
                        if target.startswith("socket:["):
                            sockets.add(target[8:-1])
                    except OSError:
                        continue
            except OSError:
                continue
        for protocol in ("tcp", "tcp6"):
            try:
                lines = Path(f"/proc/net/{protocol}").read_text().splitlines()[1:]
            except OSError:
                continue
            for line in lines:
                fields = line.split()
                if (len(fields) > 9 and fields[3] == "0A" and fields[9] in sockets
                        and int(fields[1].rsplit(":", 1)[1], 16) == port):
                    return True
        return False

    def _orphans(self, component: Component) -> list[dict]:
        _, port = self._address(component)
        if not port or not self._markers(component):
            return []
        records = []
        for path in Path("/proc").iterdir():
            if not path.name.isdigit():
                continue
            pid = int(path.name)
            if pid == os.getpid() or not self._alive(pid):
                continue
            if not self._command_matches(pid, self._markers(component), self._workdir(component)):
                continue
            state = self._stat(pid)
            group = state is not None and state[1] == pid
            if self._listeners(pid, port, group):
                records.append(self._record(component, pid, self._workdir(component), group))
        return records

    def _check_dead_group(self, record: dict, component: Component) -> None:
        """Refuse stale cleanup while uncaptured group members may still exist.

        A dead leader cannot authenticate descendants. Recheck each current group
        and start identity, but never adopt or signal it, including group-ID reuse.
        """
        pid = record.get("pid")
        if not record.get("process_group") or type(pid) is not int or pid <= 0:
            return
        for member in self._group_members(pid):
            before = self._stat(member)
            current = self._stat(member)
            if (before and current and current[0] != "Z" and current[1] == pid
                    and current[1:] == before[1:]):
                raise ProcessError(
                    f"{component.name}: saved process group {pid} has live members without captured identity; "
                    "inspect the group before retrying; record retained"
                )

    def effective_component(self, component: Component) -> Component:
        """Recover only a live, identity-checked saved launch address for lifecycle."""
        if component.mode == "remote" or component.provisioning != "process":
            return component
        record = self._read(self._path(component))
        if record is None:
            return component
        # Same-config stale records are recoverable only without live group
        # members; a dead leader cannot authenticate uncaptured descendants.
        if (not record.get("termination_members") and self._metadata_matches(record, component)
                and not self._alive(record.get("pid", 0))):
            self._check_dead_group(record, component)
            return component
        endpoint, host, port = record.get("endpoint"), record.get("host"), record.get("port")
        if ((endpoint is not None and not isinstance(endpoint, str))
                or (host is not None and not isinstance(host, str))
                or (port is not None and (type(port) is not int or not 1 <= port <= 65535))):
            raise ProcessError(f"{component.name}: saved address identity mismatch")
        effective = replace(component, endpoint=endpoint, host=host, port=port)
        try:
            valid = self._matches(record, effective)
        except (ValueError, OSError):
            valid = False
        if not valid or (port and not self._listeners(record["pid"], port, record.get("process_group", False))):
            raise ProcessError(f"{component.name}: saved process identity mismatch")
        return effective

    def status(self, component: Component) -> ProcessState:
        if component.mode == "local" and component.provisioning == "foreground":
            self._components[component.name] = component
            active = [record for _, record in self.active_tui_records() if record["project"] == component.name]
            return ProcessState("running", "managed foreground process") if active else ProcessState("stopped")
        readiness = self.probe_func(component, self.environ)
        if component.mode == "remote":
            return ProcessState(readiness.state, readiness.detail)
        record = self._read(self._path(component))
        if record and self._matches(record, component):
            _, port = self._address(component)
            listening = not port or self._listeners(record["pid"], port, record.get("process_group", False))
            if readiness.ok and listening:
                return ProcessState("running", "managed process ready")
            age = time.time() - record.get("created_at", 0)
            return ProcessState("starting" if age < 30 else "unhealthy", "managed process not ready")
        orphaned = self._orphans(component)
        if orphaned:
            return ProcessState("orphaned", "current repository process(es): " +
                                ", ".join(str(record["pid"]) for record in orphaned))
        return ProcessState("external", "unmanaged endpoint reachable") if readiness.ok else ProcessState("stopped")

    def spawn(self, component: Component, args: Sequence[str] = (), log_path: Path | None = None,
              environment: Mapping[str, str] | None = None) -> int:
        if component.mode == "remote":
            raise ProcessError(f"{component.name}: remote components cannot be spawned")
        path = self._path(component)
        if path is None:
            raise ProcessError(f"{component.name}: ICARUS_DATA_DIR is required")
        record = self._read(path)
        if record:
            if record.get("termination_members"):
                raise ProcessError(f"{component.name}: incomplete termination identity; record retained")
            if self._matches(record, component):
                return record["pid"]
            if not self._metadata_matches(record, component) or self._alive(record.get("pid", 0)):
                raise ProcessError(f"{component.name}: process identity mismatch; record retained")
            self._check_dead_group(record, component)
        elif path.exists():
            raise ProcessError(f"{component.name}: invalid process identity record retained")
        if self._orphans(component) or self.probe_func(component, self.environ).ok:
            raise ProcessError(f"{component.name}: existing endpoint/process must be handled before start")
        argv = self._argv(component, args)
        cwd = self._workdir(component)
        if not cwd.is_dir() or not self._markers(component):
            raise ProcessError(f"{component.name}: workdir and process_markers are required")
        log_path = Path(log_path) if log_path is not None else self.data_dir / "logs" / f"{component.name}.log"
        if not log_path.resolve().is_relative_to(self.data_dir):
            raise ProcessError(f"{component.name}: log path escapes ICARUS_DATA_DIR")
        log_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            with log_path.open("ab", buffering=0) as output:
                child = subprocess.Popen(argv, cwd=cwd, env=self._environment(environment),
                                         stdin=subprocess.DEVNULL, stdout=output,
                                         stderr=subprocess.STDOUT, start_new_session=True)
        except OSError:
            raise ProcessError(f"{component.name}: process start failed") from None
        self._children[child.pid] = child
        self._components[component.name] = component
        try:
            self._write(path, self._record(component, child.pid, cwd, True, log_path))
        except BaseException:
            os.killpg(child.pid, signal.SIGKILL)
            child.wait()
            raise
        return child.pid

    def _group_members(self, group: int) -> list[int]:
        return [int(path.name) for path in Path("/proc").iterdir()
                if path.name.isdigit() and (state := self._stat(int(path.name)))
                and state[1] == group and state[0] != "Z"]

    def _member_snapshot(self, pid: int) -> dict:
        try:
            return {"pid": pid, "start": self._started(pid), "group": os.getpgid(pid),
                    "session": os.getsid(pid), "cwd": str(Path(f"/proc/{pid}/cwd").resolve(strict=True)),
                    "command": Path(f"/proc/{pid}/cmdline").read_bytes().decode(errors="replace")}
        except OSError:
            raise ProcessError("process identity changed during termination capture") from None

    def _member_matches(self, member: dict) -> bool:
        try:
            return self._member_snapshot(member["pid"]) == member
        except ProcessError:
            return False

    def _terminate(self, record: dict, component: Component, cwd: Path | None = None,
                   record_path: Path | None = None) -> None:
        pid = record["pid"]
        members = record.get("termination_members")
        if members is None:
            if not self._matches(record, component, cwd):
                raise ProcessError(f"{component.name}: process identity mismatch; refusing signal")
            group = bool(record.get("process_group")) and os.getpgid(pid) == pid
            members = [self._member_snapshot(member) for member in
                       (self._group_members(pid) if group else [pid])]
            record["termination_members"] = members
            if record_path is not None:
                self._write(record_path, record)
        elif not self._metadata_matches(record, component, cwd):
            raise ProcessError(f"{component.name}: termination identity mismatch; record retained")
        # Signal individual captured identities, not a group whose leader may already have exited.
        for member in members:
            if self._alive(member["pid"]) and self._member_matches(member):
                try:
                    os.kill(member["pid"], signal.SIGTERM)
                except ProcessLookupError:
                    pass
        deadline = time.monotonic() + 3
        while any(self._alive(member["pid"]) for member in members) and time.monotonic() < deadline:
            time.sleep(.03)
        for member in members:
            if self._alive(member["pid"]) and self._member_matches(member):
                try:
                    os.kill(member["pid"], signal.SIGKILL)
                except ProcessLookupError:
                    pass
        deadline = time.monotonic() + 3
        while any(self._alive(member["pid"]) for member in members) and time.monotonic() < deadline:
            time.sleep(.03)
        if (any(self._alive(member["pid"]) for member in members)
                or (record.get("process_group") and self._group_members(pid))):
            raise ProcessError(f"{component.name}: termination identity changed or survivors remain; record retained")
        child = self._children.pop(pid, None)
        if child is not None:
            child.wait(timeout=3)

    def stop(self, component: Component) -> None:
        if component.mode == "remote":
            return
        if component.provisioning == "foreground":
            self._components[component.name] = component
            for path, record in self.active_tui_records():
                if record["project"] == component.name:
                    self._terminate(record, component, Path(record["workdir"]), path)
                    self._remove(path, record)
            return
        path = self._path(component)
        record = self._read(path)
        if record:
            if not self._metadata_matches(record, component):
                raise ProcessError(f"{component.name}: process identity mismatch; record retained")
            if self._alive(record.get("pid", 0)) or record.get("termination_members"):
                self._terminate(record, component, record_path=path)
            else:
                self._check_dead_group(record, component)
            self._remove(path, record)
            return
        if path and path.exists():
            raise ProcessError(f"{component.name}: invalid process identity record retained")
        for record in self._orphans(component):
            self._terminate(record, component, record_path=path)
            if path is not None:
                self._remove(path, record)

    def foreground(self, component: Component, args: Sequence[str] = (),
                   environment: Mapping[str, str] | None = None, *, cwd: Path | None = None) -> int:
        if component.mode == "remote" or self.data_dir is None or not self._markers(component):
            raise ProcessError(f"{component.name}: foreground requires local metadata and ICARUS_DATA_DIR")
        self._path(component)  # Validate the component name before constructing a directory.
        cwd = Path(cwd or Path.cwd()).resolve()
        argv = self._argv(component, args)
        try:
            child = subprocess.Popen(argv, cwd=cwd, env=self._environment(environment))
        except OSError:
            raise ProcessError(f"{component.name}: foreground start failed") from None
        self._children[child.pid] = child
        self._components[component.name] = component
        path = self.data_dir / "runtime" / component.name / f"{child.pid}.json"
        record = self._record(component, child.pid, cwd, False)
        try:
            self._write(path, record)
            try:
                return child.wait()
            except KeyboardInterrupt:
                self._terminate(record, component, cwd)
                return child.wait()
        finally:
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()
            self._remove(path, record)
            self._children.pop(child.pid, None)

    def active_tui_records(self) -> list[tuple[Path, dict[str, object]]]:
        if self.data_dir is None:
            return []
        active = []
        for path in (self.data_dir / "runtime").glob("*/*.json"):
            record = self._read(path)
            if not record or not record.get("foreground") or record.get("repo_root") != str(self.repo_root):
                continue
            name = record.get("project")
            component = self._components.get(name)
            if component is None and name in self.markers:
                component = Component(name, "app", "local", "foreground",
                                      process_markers=self.markers[name], endpoint=record.get("endpoint"),
                                      host=record.get("host"), port=record.get("port"))
            if component and self._matches(record, component, Path(record.get("workdir", ""))):
                active.append((path, record))
        return active

    def logs(self, component: Component, follow: bool = True) -> int:
        if self.data_dir is None:
            raise ProcessError(f"{component.name}: ICARUS_DATA_DIR is required")
        record = self._read(self._path(component))
        recorded_log = record.get("log") if record and self._metadata_matches(record, component) else None
        path = Path(recorded_log) if isinstance(recorded_log, str) else self.data_dir / "logs" / f"{component.name}.log"
        if not path.resolve().is_relative_to(self.data_dir):
            raise ProcessError(f"{component.name}: log path escapes ICARUS_DATA_DIR")
        try:
            with path.open() as source:
                while True:
                    text = source.read()
                    if text:
                        print(text, end="", flush=True)
                    if not follow:
                        return 0
                    time.sleep(.1)
        except KeyboardInterrupt:
            return 0
        except OSError:
            raise ProcessError(f"{component.name}: process log unavailable") from None
