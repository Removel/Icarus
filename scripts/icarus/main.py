#!/usr/bin/env python3
"""Thin stdlib CLI facade for the registry-driven control plane."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Mapping, Sequence

from compose_runtime import ComposeError, ComposeRuntime
from configuration import address_override, configuration_document, load_configuration
from environment import expand_braced_variables, merged_environment, read_env_file
from graph import GraphError
from lifecycle import Lifecycle
from process_runtime import ProcessError, ProcessRuntime
from registry import RegistryError

REPO_ROOT = Path(__file__).resolve().parents[2]


class ControlError(RuntimeError):
    """An actionable CLI error."""


class IcarusControl:
    def __init__(self, repo_root: Path = REPO_ROOT, *, cwd: Path | None = None,
                 environ: Mapping[str, str] | None = None):
        self.repo_root = repo_root.resolve()
        self.cwd = (cwd or Path.cwd()).resolve()
        self.environ = dict(os.environ if environ is None else environ)

    def print_help(self):
        print("""Usage: icarus [--config PATH] COMMAND [COMPONENT] [OPTIONS]
  config validate                 Validate all enabled topology, without Docker or secrets
  config show [COMPONENT]          Show effective registry and source paths (no secret resolution)
  install [COMPONENT] [--dev]      Install local software/images; remote components are untouched
  start [COMPONENT]                Start required dependencies; default stack opens foreground last
  stop [COMPONENT]                 Stop only named runtime, or reverse default dependency closure
  status [COMPONENT]               Show mode, address, state and dependency degradation
  logs COMPONENT [--no-follow]     Read/follow local runtime logs
  tui [OPTIONS]                   Open registered foreground UI without starting its dependencies

Invocation-only explicit-component overrides: --mode local|remote --host HOST --port PORT --endpoint URL
--config PATH works before or after COMMAND, once only. Its sibling <stem>.local.toml overlays it.
Operational paths always resolve from the repository. Legacy 'icarus --session-id ID' opens the UI.""")

    def _runtime(self, registry, *, dev=False, foreground_args=()):
        values = merged_environment(self.repo_root, self.environ)
        data_value = values.get("ICARUS_DATA_DIR", "")
        data_value = expand_braced_variables(data_value, values)
        data_dir = Path(data_value).expanduser() if data_value else None
        if data_dir is not None:
            if not data_dir.is_absolute():
                raise ControlError("ICARUS_DATA_DIR must be an absolute path")
            data_dir = data_dir.resolve()
            values["ICARUS_DATA_DIR"] = str(data_dir)
        markers = {c.name: c.process_markers for c in registry.components.values()
                   if c.provisioning == "foreground"}
        process = ProcessRuntime(self.repo_root, data_dir, values, markers)
        compose = ComposeRuntime(self.repo_root, data_dir, values, registry)
        def installer(component):
            command = component.dev_install_command if dev and component.dev_install_command else component.install_command
            if not command:
                return 0
            substitutions = {"repo_root": str(self.repo_root), "python": sys.executable,
                             "data_dir": str(data_dir) if data_dir else ""}
            try:
                argv = [part.format_map(substitutions) for part in command]
            except (KeyError, ValueError):
                raise ControlError(f"{component.name}: unsupported install argv placeholder") from None
            return self._run_checked(argv, values)
        lifecycle = Lifecycle(registry, compose=compose, process=process, installer=installer,
                              environment=values, process_args=lambda c: foreground_args if c.provisioning == "foreground" else ())
        if dev:
            # Dev installs are app-owned environments, not Compose build operations.
            def install_dev(names):
                from graph import required_closure
                result = 0
                for name in required_closure(registry, registry.default_stack() if names is None else names):
                    component = registry.get(name)
                    if lifecycle.owner(component).mode != "remote" and component.kind == "app":
                        result |= installer(component)
                return result
            lifecycle.install = install_dev
        return lifecycle

    def _run_checked(self, command, environment=None):
        try:
            result = subprocess.run(list(command), cwd=self.repo_root,
                                    env=environment or self.environ, check=False)
        except OSError:
            raise ControlError("installer command unavailable") from None
        if result.returncode:
            raise ControlError(f"installer failed (exit {result.returncode})")
        return 0

    def run(self, argv: Sequence[str]) -> int:
        args = list(argv)
        config = None
        # Strip exactly one global selector, supporting either side of the verb.
        positions = [i for i, value in enumerate(args) if value == "--config" or value.startswith("--config=")]
        if len(positions) > 1:
            raise ControlError("--config may appear only once")
        if positions:
            index = positions[0]
            value = args.pop(index)
            if value == "--config":
                if index >= len(args) or args[index].startswith("-"):
                    raise ControlError("--config requires a path")
                config = args.pop(index)
            else:
                config = value.partition("=")[2]
                if not config:
                    raise ControlError("--config requires a path")
        if args and args[0] in {"help", "--help", "-h"}:
            self.print_help()
            return 0
        command = args.pop(0) if args and not args[0].startswith("-") else "tui"
        if command not in {"config", "install", "start", "stop", "status", "logs", "tui"}:
            raise ControlError(f"unknown command: {command}; run 'icarus help'")
        if args and args[0] in {"-h", "--help"}:
            self.print_help()
            return 0
        registry = load_configuration(self.repo_root, config)
        if command == "config":
            if not args or args[0] not in {"validate", "show"}:
                raise ControlError("config requires validate or show [component]")
            action = args.pop(0)
            if len(args) > (1 if action == "show" else 0):
                raise ControlError("unexpected config argument")
            if action == "validate":
                print(f"configuration valid ({len(registry.components)} components)")
            else:
                print(json.dumps(configuration_document(registry, self.repo_root, config, args[0] if args else None), indent=2, ensure_ascii=False))
            return 0
        foreground = next((c for c in registry.components.values() if c.enabled and c.provisioning == "foreground"), None)
        if command == "tui":
            if foreground is None:
                raise ControlError("no foreground component registered")
            return self._runtime(registry).run_foreground(foreground, args, cwd=self.cwd)
        name = args.pop(0) if args and not args[0].startswith("-") else None
        component = registry.get(name) if name else None
        if component and not component.enabled:
            raise ControlError(f"{name}: disabled component")
        if component and component.provisioning == "embedded" and command in {"start", "stop"}:
            raise ControlError(f"{name} runs inside {component.owner}; use 'icarus {command} {component.owner}'")
        if command == "start" and ((component and component.provisioning == "foreground") or component is None):
            # Only topology overrides are intercepted; remaining flags belong to the UI.
            tui_args = []
            overrides = {}
            index = 0
            while index < len(args):
                flag = args[index].split("=", 1)[0]
                if flag in {"--mode", "--host", "--port", "--endpoint"}:
                    if component is None:
                        raise ControlError("topology overrides require an explicit component")
                    raise ControlError("foreground address overrides are not applicable; use UI endpoint options")
                tui_args.append(args[index])
                index += 1
            if component:
                return self._runtime(registry).run_foreground(component, tui_args, cwd=self.cwd)
            lifecycle = self._runtime(registry, foreground_args=tui_args)
            # Lifecycle schedules foreground last; preserve the caller workspace.
            original = lifecycle.run_foreground
            lifecycle.run_foreground = lambda c, a=(): original(c, a, cwd=self.cwd)
            return lifecycle.start(None)
        parser = argparse.ArgumentParser(add_help=False, exit_on_error=False)
        parser.add_argument("--mode", choices=("local", "remote"))
        parser.add_argument("--host")
        parser.add_argument("--port", type=int)
        parser.add_argument("--endpoint")
        parser.add_argument("--dev", action="store_true")
        parser.add_argument("--no-follow", action="store_true")
        try:
            options, extra = parser.parse_known_args(args)
        except argparse.ArgumentError:
            raise ControlError("invalid command options") from None
        if extra:
            raise ControlError("unexpected command arguments")
        overrides = {key: value for key, value in vars(options).items() if key in {"mode", "host", "port", "endpoint"} and value is not None}
        if overrides:
            if component is None:
                raise ControlError("topology overrides require an explicit component")
            registry = load_configuration(self.repo_root, config, {name: address_override(component, overrides)})
        if options.dev and command != "install":
            raise ControlError("--dev applies only to install")
        if options.no_follow and command != "logs":
            raise ControlError("--no-follow applies only to logs")
        lifecycle = self._runtime(registry, dev=options.dev)
        names = [name] if name else None
        if command == "status":
            print(f"{'COMPONENT':<18} {'MODE':<8} {'STATE':<12} DETAIL", flush=True)

            def emit(row):
                print(f"{row.component:<18} {row.mode:<8} {row.state:<12} {row.detail}", flush=True)

            lifecycle.status(names, on_row=emit)
            return 0
        if command == "logs":
            if name is None:
                raise ControlError("logs requires an explicit component")
            return lifecycle.logs(name, follow=not options.no_follow)
        return getattr(lifecycle, command)(names)


def main(argv=None):
    try:
        return IcarusControl().run(sys.argv[1:] if argv is None else argv)
    except (ControlError, RegistryError, GraphError, ComposeError, ProcessError, ValueError) as error:
        print(f"icarus: {error}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
