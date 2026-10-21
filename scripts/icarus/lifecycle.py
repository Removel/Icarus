"""Component lifecycle orchestration; topology and commands come only from the registry."""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
import socket
import time
from urllib.parse import urlsplit, urlunsplit

from graph import required_closure, reverse_order
from injection import build_child_environment
from probes import probe
from registry import Component, Registry, RegistryError


@dataclass(frozen=True)
class StatusRow:
    component: str
    mode: str
    state: str
    detail: str


def _write_line(message):
    print(message, flush=True)


class Lifecycle:
    def __init__(self, registry: Registry, *, compose, process, probe_func=probe,
                 inject_func=build_child_environment, installer=None,
                 process_args=lambda component: (), output=_write_line,
                 environment=None, readiness_timeout=180, progress_interval=5):
        self.registry = registry
        self.compose = compose
        self.process = process
        self.probe = probe_func
        self.inject = inject_func
        self.installer = installer
        self.process_args = process_args
        self.output = output
        self.environment = dict(environment if environment is not None else getattr(process, "environ", {}))
        self.readiness_timeout = readiness_timeout
        self.progress_interval = progress_interval

    def owner(self, component):
        while component.mode == "local" and component.provisioning in {"embedded", "bundled"}:
            component = self.registry.get(component.owner)
        return component

    def _hosted(self, component, provisioning=None):
        return [child for child in self.registry.components.values()
                if child.enabled and child.mode == "local" and child.name != component.name
                and child.provisioning in ({provisioning} if provisioning else {"embedded", "bundled"})
                and self.owner(child).name == component.name]

    @staticmethod
    def address(component):
        return component.endpoint or (f"{component.host}:{component.port}" if component.host else "-")

    def _export_keys(self, component):
        keys = set(component.env) | set(component.secret_env)
        keys.update(k for k in (component.host_env, component.port_env, component.endpoint_env) if k)
        for edge in component.requires.values():
            if edge.endpoint_env:
                keys.add(edge.endpoint_env)
            if edge.connection:
                keys.update(value for key, value in vars(edge.connection).items()
                            if key.endswith("_env") and key != "password_env" and value)
        return keys

    def child_environment(self, component, *, passive=False):
        """Union runtime and hosted exports, rejecting conflicting declared bindings."""
        members = [component, *self._hosted(component)]
        base = dict(self.environment)
        if passive:
            base.setdefault("ICARUS_DATA_DIR", "/tmp/icarus-passive-interpolation")
            for member in members:
                for source in member.secret_env.values():
                    if not base.get(source):
                        base[source] = "not-used-offline"
        environment = dict(base)
        exports = {}
        for member in members:
            result = self.inject(member, self.registry, base)
            for warning in result.warnings:
                self.output(f"warning: {warning}")
            for key in self._export_keys(member):
                if key not in result.environment:
                    continue
                value = result.environment[key]
                if key in exports and exports[key] != value:
                    raise RegistryError(f"{component.name}: conflicting hosted export {key}")
                exports[key] = value
                environment[key] = value
        return environment

    def _dependencies(self, component):
        members = [component, *self._hosted(component, "embedded")]
        for member in members:
            for edge in member.requires.values():
                dependency = self.registry.get(edge.name)
                # Hosted children are logical readiness nodes, not owner prerequisites.
                if dependency.mode == "local" and self.owner(dependency).name == component.name:
                    continue
                yield member, edge, dependency

    def _dependency_failures(self, component, outcomes=None):
        failures = []
        for member, edge, dependency in self._dependencies(component):
            if not dependency.enabled:
                ready = False
            elif dependency.mode == "local" and dependency.provisioning == "embedded":
                runtime = self.owner(dependency)
                # A logical node's successful preflight is not evidence that its
                # runtime is serving an unrelated consumer. Intra-owner edges
                # were already excluded by _dependencies before this check.
                if outcomes is not None and runtime.name in outcomes:
                    ready = outcomes[runtime.name]
                else:
                    ready = self.probe(runtime, self.environment).ok
                if ready and runtime.mode != "remote":
                    ready = not any(required for _, required in self._dependency_failures(dependency, outcomes))
            elif outcomes is not None and dependency.name in outcomes:
                ready = outcomes[dependency.name]
            else:
                ready = self.probe(self.owner(dependency), self.environment).ok
            if not ready:
                chain = " -> ".join(dict.fromkeys((component.name, member.name, dependency.name)))
                failures.append((chain, edge.required))
        return failures

    def _selected(self, names):
        return required_closure(self.registry, self.registry.default_stack() if names is None else names)

    def install(self, names: Sequence[str] | None) -> int:
        failed = False
        seen = set()
        for name in self._selected(names):
            component = self.registry.get(name)
            runtime = self.owner(component)
            # Embedded software still has its own installer; bundled services do not.
            if component.provisioning == "bundled":
                component = runtime
            if runtime.mode == "remote" or component.mode == "remote":
                self.output(f"{component.name}: remote component not managed; install skipped")
                continue
            if component.name in seen:
                continue
            seen.add(component.name)
            try:
                if component.provisioning == "compose":
                    self.compose.build(component, self.child_environment(component, passive=True))
                elif self.installer:
                    result = self.installer(component)
                    if result:
                        raise RuntimeError(f"{component.name}: install failed (exit {result})")
            except (RuntimeError, ValueError, OSError) as error:
                self.output(str(error))
                failed = True
        return int(failed)

    def _effective(self, component):
        if component.mode == "local" and component.provisioning == "process":
            return self.process.effective_component(component)
        return component

    def _raw_status(self, component):
        runtime = self.owner(component)
        if runtime.name != component.name:
            row = self._raw_status(runtime)
            return StatusRow(component.name, runtime.mode, row.state,
                             f"{component.provisioning} in {runtime.name}; {row.detail}")
        if component.mode == "remote":
            result = self.probe(component, self.environment)
            return StatusRow(component.name, component.mode, result.state,
                             f"{self.address(component)}; {result.detail}; reachable is not application/auth readiness")
        if component.provisioning == "process":
            current = self.process.status(component)
            if current.state not in {"running", "starting", "unhealthy"}:
                component = self._effective(component)
        address = self.address(component)
        if component.provisioning in {"process", "foreground"}:
            result = self.process.status(component)
            detail = "; ".join(part for part in (address, result.detail) if part)
            return StatusRow(component.name, component.mode, result.state, detail)
        if component.provisioning == "compose":
            rows = self.compose.containers(component)
            ready = self.probe(component, self.environment).ok
            running = [r for r in rows if str(r.get("State", "")).lower() == "running"]
            health = {str(r.get("HealthStatus", r.get("Health", ""))).lower() for r in running}
            if not rows:
                state = "external" if ready else "stopped"
            elif len(running) < component.expected_containers or "starting" in health:
                state = "starting" if running else "stopped"
            elif "unhealthy" in health or not ready:
                state = "unhealthy"
            else:
                state = "running"
            return StatusRow(component.name, component.mode, state,
                             f"{address}; {len(running)}/{component.expected_containers} containers running")
        result = self.probe(component, self.environment)
        return StatusRow(component.name, component.mode, result.state, f"{address}; {result.detail}")

    def status(self, names: Sequence[str] | None, *, on_row=None) -> tuple[StatusRow, ...]:
        rows = []

        def record(row):
            rows.append(row)
            if on_row is not None:
                on_row(row)

        for name in self.registry.names() if names is None else names:
            component = self.registry.get(name)
            if not component.enabled:
                record(StatusRow(name, component.mode, "disabled", self.address(component)))
                continue
            try:
                row = self._raw_status(component)
                if self.owner(component).mode != "remote":
                    failures = self._dependency_failures(component)
                    if failures:
                        blocking = any(required for _, required in failures)
                        detail = "; ".join(("blocked dependency " if required else "optional dependency ") + chain
                                           for chain, required in failures)
                        label = "degraded; " if blocking else "degraded optional; "
                        row = StatusRow(name, row.mode, "degraded" if row.state in {"running", "external"} else row.state,
                                        "; ".join(part for part in (row.detail, label + detail) if part))
                record(row)
            except (RuntimeError, ValueError, OSError) as error:
                record(StatusRow(name, component.mode, "unknown", f"{self.address(component)}; {error}; stop/restart with the original launch configuration if identity changed"))
        return tuple(rows)

    def _wait(self, component):
        self.output(f"{component.name}: waiting for readiness ({self.address(component)})")
        started = time.monotonic()
        deadline = started + self.readiness_timeout
        progress = self.progress_interval if self.progress_interval > 0 else None
        next_progress = started + progress if progress is not None else None
        while True:
            row = self._raw_status(component)
            if row.state in {"running", "external", "reachable"}:
                # Bundled services are supplied atomically by owner Compose, not prestarted.
                children = self._hosted(component, "bundled")
                if all(self.probe(child, self.environment).ok for child in children):
                    return
            if component.provisioning == "process" and row.state == "stopped":
                raise RuntimeError(f"{component.name}: exited before readiness; inspect logs")
            now = time.monotonic()
            if next_progress is not None and now >= next_progress:
                self.output(f"{component.name}: still waiting for readiness ({now - started:.0f}s)")
                while next_progress <= now:
                    next_progress += progress
            if now >= deadline:
                raise RuntimeError(f"{component.name}: readiness timeout; inspect status/logs")
            time.sleep(.1)

    @staticmethod
    def _port_open(component):
        if not component.host or not component.port:
            return False
        try:
            with socket.create_connection((component.host, component.port), timeout=.2):
                return True
        except OSError:
            return False

    def start(self, names: Sequence[str] | None) -> int:
        outcomes = {}
        foreground = []
        failed = False
        for name in self._selected(names):
            component = self.registry.get(name)
            if component.provisioning == "foreground" and component.mode == "local":
                foreground.append(component)
                continue
            try:
                runtime = self.owner(component)
                if runtime.mode == "remote":
                    result = self.probe(runtime, self.environment)
                    self.output(f"{name}: {result.state} ({self.address(runtime)}; {result.detail})")
                    outcomes[name] = result.ok
                    failed |= not result.ok
                    continue
                issues = self._dependency_failures(component, outcomes)
                for chain, required in issues:
                    self.output(f"{'blocked' if required else 'warning'}: {chain} unavailable")
                if any(required for _, required in issues):
                    outcomes[name] = False
                    failed = True
                    continue
                if component.provisioning == "embedded":
                    outcomes[name] = True
                    continue
                row = self._raw_status(component)
                if row.state in {"running", "external", "reachable"}:
                    self.output(f"{name}: already {row.state} ({row.detail}); configuration changes require restart")
                else:
                    environment = self.child_environment(component)
                    if row.state == "unhealthy":
                        raise RuntimeError(f"{name}: unhealthy; stop before restarting")
                    if component.provisioning == "process":
                        if row.state == "orphaned":
                            self.process.stop(component)
                        if row.state != "starting":
                            if self._port_open(component):
                                raise RuntimeError(f"{name}: port in use by an unrecognized service; refusing start")
                            self.output(f"{name}: starting {component.provisioning} ({self.address(component)})")
                            self.process.spawn(component, self.process_args(component), environment=environment)
                    elif component.provisioning == "compose":
                        if row.state != "starting":
                            if self._port_open(component):
                                raise RuntimeError(f"{name}: port in use by an unrecognized service; refusing start")
                            self.output(f"{name}: starting {component.provisioning} ({self.address(component)})")
                            self.compose.up(component, environment)
                    elif component.provisioning != "none":
                        raise RuntimeError(f"{name}: unsupported start provisioning")
                    self._wait(component)
                    self.output(f"{name}: running ({self.address(component)})")
                outcomes[name] = True
            except (RuntimeError, ValueError, OSError) as error:
                outcomes[name] = False
                failed = True
                self.output(f"{name}: {error}")
        # Foreground consumers are always last; unrelated branch failures are retained.
        for component in foreground:
            try:
                issues = self._dependency_failures(component, outcomes)
                for chain, required in issues:
                    self.output(f"{'blocked' if required else 'warning'}: {chain} unavailable")
                if any(required for _, required in issues):
                    failed = True
                    continue
                self.output(f"{component.name}: opening foreground ({self.address(component)})")
                result = self.run_foreground(component, self.process_args(component))
                failed |= result != 0
            except (RuntimeError, ValueError, OSError) as error:
                self.output(str(error))
                failed = True
        return int(failed)

    def run_foreground(self, component, args=(), *, cwd: Path | None = None):
        args = list(args)
        supplied = {value.split("=", 1)[0] for value in args if value.startswith("--")}
        bypass = set()
        for flag, name in component.endpoint_args.items():
            if flag in supplied:
                bypass.add(name)
                continue
            target = self.registry.get(name)
            url = urlsplit(target.endpoint or "")
            scheme = {"http": "ws", "https": "wss"}.get(url.scheme, url.scheme)
            path = url.path.rstrip("/")
            path = path if path.endswith("/rpc") else path + "/rpc"
            args.extend((flag, urlunsplit(url._replace(scheme=scheme, path=path))))
        for _, edge, dependency in self._dependencies(component):
            if edge.required and dependency.name not in bypass and not self.probe(self.owner(dependency), self.environment).ok:
                raise RuntimeError(f"{component.name} -> {dependency.name}: not ready; start {dependency.name} first")
        return self.process.foreground(component, args, self.child_environment(component), cwd=cwd)

    def stop(self, names: Sequence[str] | None) -> int:
        order = names if names is not None else reverse_order(self._selected(None))
        seen = set()
        failed = False
        for name in order:
            component = self.owner(self.registry.get(name))
            if component.name in seen:
                continue
            seen.add(component.name)
            try:
                if component.mode == "remote":
                    self.output(f"{name}: remote component not managed; left running")
                    continue
                component = self._effective(component)
                if component.provisioning in {"process", "foreground"}:
                    self.process.stop(component)
                elif component.provisioning == "compose":
                    if self.compose.containers(component):
                        self.compose.down(component, self.child_environment(component, passive=True))
                    else:
                        self.output(f"{name}: no managed containers; external service left untouched")
                        continue
                self.output(f"{name}: stopped (data preserved)")
            except (RuntimeError, ValueError, OSError) as error:
                self.output(f"{name}: {error}; identity record retained; use original configuration")
                failed = True
        return int(failed)

    def logs(self, name: str, follow=True) -> int:
        component = self.owner(self.registry.get(name))
        if component.mode == "remote" or component.provisioning == "foreground":
            self.output(f"{name}: no local independent logs ({component.mode} {component.provisioning})")
            return 0
        if component.provisioning == "compose":
            return self.compose.logs(component, follow, self.child_environment(component, passive=True))
        return self.process.logs(component, follow)
