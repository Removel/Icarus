"""Lazy, argv-only Compose operations; application wrappers retain validation."""
from __future__ import annotations

from collections.abc import Callable, Mapping
import json
import os
from pathlib import Path
import subprocess

from environment import environment_file
from registry import Component, Registry


class ComposeError(RuntimeError):
    """A local Compose operation could not be completed."""


class ComposeRuntime:
    def __init__(
        self, repo_root: Path, data_dir: Path | None, environ: Mapping[str, str],
        registry: Registry, runner: Callable[..., subprocess.CompletedProcess] | None = None,
    ):
        self.repo_root = Path(repo_root).resolve()
        self.data_dir = Path(data_dir).resolve() if data_dir is not None else None
        self.environ = dict(environ)
        self.registry = registry
        self.runner = runner or subprocess.run
        self._client: list[str] | None = None

    def _run(self, argv: list[str], *, environment=None, capture=True, timeout=30):
        try:
            return self.runner(argv, cwd=self.repo_root, env=environment or dict(self.environ),
                               check=False, text=True, capture_output=capture, timeout=timeout)
        except (OSError, subprocess.TimeoutExpired):
            raise ComposeError("Docker Compose command unavailable or timed out") from None

    def _compose_client(self) -> list[str]:
        if self._client is None:
            for argv in (["docker", "compose"], ["docker-compose"]):
                try:
                    result = self._run([*argv, "version"])
                except ComposeError:
                    continue
                if result.returncode == 0:
                    self._client = argv
                    break
            if self._client is None:
                raise ComposeError("Docker Compose plugin or docker-compose is required")
        return self._client

    def _scope(self, component: Component) -> tuple[str, Path]:
        if not component.compose or not component.compose_project:
            raise ComposeError(f"{component.name}: compose file and project are required")
        path = (self.repo_root / component.compose).resolve()
        if not path.is_relative_to(self.repo_root) or not path.is_file():
            raise ComposeError(f"{component.name}: compose file is missing or outside repository")
        return component.compose_project, path

    def _environment(self, component: Component, injected=None) -> dict[str, str]:
        project, path = self._scope(component)
        environment = {**self.environ, **(injected or {})}
        if self.data_dir is not None:
            environment["ICARUS_DATA_DIR"] = str(self.data_dir)
        # Wrappers own argv (including their -f); scope is passed without moving the action.
        environment["COMPOSE_PROJECT_NAME"] = project
        environment["COMPOSE_FILE"] = str(path)
        return environment

    def ensure_network(self, name: str = "icarus") -> None:
        if self._run(["docker", "network", "inspect", name]).returncode == 0:
            return
        if self._run(["docker", "network", "create", name]).returncode != 0:
            # Another start may have created the same shared network concurrently.
            if self._run(["docker", "network", "inspect", name]).returncode != 0:
                raise ComposeError("could not create shared Docker network")

    def command(self, component: Component, *args: str) -> list[str]:
        project, path = self._scope(component)
        if component.compose_command:
            return [*component.compose_command, *args]
        env_file = environment_file(self.repo_root, self.environ)
        return [*self._compose_client(), "--env-file",
                str(env_file) if env_file.is_file() else os.devnull,
                "-p", project, "-f", str(path), *args]

    def _action(self, component: Component, *args: str, environment=None) -> None:
        result = self._run(self.command(component, *args),
                           environment=self._environment(component, environment), timeout=None)
        if result.returncode != 0:
            # Child output may contain credentials: do not include it in errors.
            raise ComposeError(f"{component.name}: Compose {args[0]} failed (exit {result.returncode})")

    def build(self, component: Component, environment: Mapping[str, str] | None = None) -> None:
        if component.mode == "remote":
            return
        if component.image_action not in {"build", "pull"}:
            raise ComposeError(f"{component.name}: invalid image_action")
        self._action(component, component.image_action, environment=environment)

    def up(self, component: Component, environment: Mapping[str, str] | None = None) -> None:
        if component.mode == "remote":
            return
        self._scope(component)
        if component.data_dir:
            if self.data_dir is None:
                raise ComposeError(f"{component.name}: ICARUS_DATA_DIR is required")
            path = (self.data_dir / component.data_dir).resolve()
            if not path.is_relative_to(self.data_dir):
                raise ComposeError(f"{component.name}: data directory escapes ICARUS_DATA_DIR")
            path.mkdir(parents=True, exist_ok=True)
        if component.shared_network:
            self.ensure_network(component.network)
        self._action(component, "up", "-d", environment=environment)

    def down(self, component: Component, environment: Mapping[str, str] | None = None) -> None:
        if component.mode != "remote":
            self._action(component, "down", environment=environment)

    def logs(self, component: Component, follow: bool = True,
             environment: Mapping[str, str] | None = None) -> int:
        if component.mode == "remote":
            return 0
        return self._run(self.command(component, "logs", *(("-f",) if follow else ())),
                         environment=self._environment(component, environment),
                         capture=False, timeout=None).returncode

    def containers(self, component: Component) -> list[dict[str, object]]:
        if component.mode == "remote":
            return []
        project, path = self._scope(component)
        result = self._run(["docker", "ps", "-a", "--filter",
                            f"label=com.docker.compose.project={project}", "--filter",
                            f"label=com.docker.compose.project.config_files={path}",
                            "--format", "{{json .}}"], timeout=3)
        if result.returncode != 0:
            raise ComposeError(f"{component.name}: could not inspect Compose containers")
        rows = []
        for line in (result.stdout or "").splitlines():
            try:
                value = json.loads(line)
            except ValueError:
                continue
            if isinstance(value, dict):
                rows.append(value)
        return rows
