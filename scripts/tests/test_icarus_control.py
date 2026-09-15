from __future__ import annotations

from contextlib import redirect_stdout
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


CONTROL_DIR = Path(__file__).resolve().parents[1] / "icarus"
if str(CONTROL_DIR) not in sys.path:
    sys.path.insert(0, str(CONTROL_DIR))

import main as control_module
from main import REPO_ROOT, ControlError, IcarusControl, ProjectStatus
from environment import expand_braced_variables, read_env_file


class IcarusControlTest(unittest.TestCase):
    def test_default_repository_root_points_to_checkout(self):
        self.assertEqual(REPO_ROOT, Path(__file__).resolve().parents[2])
        self.assertTrue((REPO_ROOT / "apps/agent").is_dir())

    def make_control(self, root: Path, *, cwd: Path | None = None) -> IcarusControl:
        (root / "scripts").mkdir(parents=True, exist_ok=True)
        (root / "apps/agent/scripts").mkdir(parents=True, exist_ok=True)
        (root / "apps/gateway/scripts").mkdir(parents=True, exist_ok=True)
        (root / "apps/tui/scripts").mkdir(parents=True, exist_ok=True)
        (root / "apps/mem0/scripts").mkdir(parents=True, exist_ok=True)
        (root / "apps/openkb/scripts").mkdir(parents=True, exist_ok=True)
        return IcarusControl(root, cwd=cwd or root, environ={})

    def test_start_all_orders_dependencies_then_opens_tui(self):
        with tempfile.TemporaryDirectory() as directory:
            control = self.make_control(Path(directory))
            events: list[object] = []
            control.start_background = lambda project: events.append(project)
            control.run_tui = lambda args: events.append(("tui", list(args))) or 7

            result = control.run(["start", "--session-id", "demo"])

            self.assertEqual(
                events,
                ["mem0", "openkb", "gateway", ("tui", ["--session-id", "demo"])],
            )
            self.assertEqual(result, 7)

    def test_legacy_tui_arguments_and_tui_command_use_same_path(self):
        with tempfile.TemporaryDirectory() as directory:
            control = self.make_control(Path(directory))
            seen: list[list[str]] = []
            control.run_tui = lambda args: seen.append(list(args)) or 0

            self.assertEqual(control.run(["--session-id", "old"]), 0)
            self.assertEqual(control.run(["tui", "--session-id", "new"]), 0)
            self.assertEqual(
                seen,
                [["--session-id", "old"], ["--session-id", "new"]],
            )

    def test_remote_tui_gateway_does_not_require_default_local_gateway(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            control = self.make_control(root)
            (root / "apps/tui/.venv/bin").mkdir(parents=True)
            (root / "apps/tui/.venv/bin/python").touch()
            (root / "apps/tui/scripts/start.sh").touch()
            control._healthy = lambda project: False

            with patch.object(control_module.subprocess, "Popen") as popen:
                popen.return_value.pid = 123
                popen.return_value.wait.return_value = 0
                with patch.object(control, "_write_process_record"), patch.object(
                    control, "_remove_record_if_pid"
                ), patch.object(
                    control, "_tui_record_path", return_value=root / "tui.json"
                ):
                    result = control.run(
                        ["tui", "--gateway-url", "ws://host.example/rpc"]
                    )

            self.assertEqual(result, 0)

    def test_agent_has_no_independent_lifecycle(self):
        with tempfile.TemporaryDirectory() as directory:
            control = self.make_control(Path(directory))
            with self.assertRaisesRegex(ControlError, "inside gateway"):
                control.run(["start", "agent"])
            with self.assertRaisesRegex(ControlError, "inside gateway"):
                control.run(["stop", "agent"])

    def test_install_single_python_app_uses_its_private_installer(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            control = self.make_control(root)
            (root / ".example.env").write_text("ICARUS_DATA_DIR=\n")
            commands: list[list[str]] = []
            control._run_checked = lambda command: commands.append(list(command))

            self.assertEqual(control.run(["install", "tui", "--dev"]), 0)

            self.assertEqual(
                commands,
                [
                    [str(root / "apps/tui/scripts/install.sh"), "--dev"],
                    [str(root / "scripts/install-commands.sh")],
                ],
            )
            self.assertFalse((root / ".venv").exists())
            self.assertEqual((root / ".env").read_text(), "ICARUS_DATA_DIR=\n")

    def test_install_service_builds_its_app_owned_image(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            control = self.make_control(root)
            (root / ".example.env").write_text("ICARUS_DATA_DIR=\n")
            commands: list[list[str]] = []
            control._check_docker = lambda: None
            control._run_checked = lambda command: commands.append(list(command))

            self.assertEqual(control.run(["install", "mem0"]), 0)

            self.assertEqual(
                commands,
                [
                    ["bash", str(root / "apps/mem0/scripts/icarus-compose.sh"), "build"],
                    [str(root / "scripts/install-commands.sh")],
                ],
            )

    def test_install_all_delegates_app_installs_then_service_builds(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            control = self.make_control(root)
            (root / ".example.env").write_text("ICARUS_DATA_DIR=\n")
            commands: list[list[str]] = []
            control._check_docker = lambda: None
            control._run_checked = lambda command: commands.append(list(command))

            self.assertEqual(control.run(["install", "--dev"]), 0)

            self.assertEqual(
                commands,
                [
                    [str(root / "scripts/install.sh"), "--dev"],
                    ["bash", str(root / "apps/mem0/scripts/icarus-compose.sh"), "build"],
                    ["bash", str(root / "apps/openkb/scripts/icarus-compose.sh"), "build"],
                    [str(root / "scripts/install-commands.sh")],
                ],
            )

    def test_start_keeps_external_healthy_service_untouched(self):
        with tempfile.TemporaryDirectory() as directory:
            control = self.make_control(Path(directory))
            control.project_status = lambda project: ProjectStatus(
                project, "external", "http://example"
            )
            called = []
            control._run_checked = lambda command: called.append(command)

            output = io.StringIO()
            with redirect_stdout(output):
                control.start_background("gateway")

            self.assertEqual(called, [])
            self.assertIn("available externally", output.getvalue())

    def test_stop_keeps_external_gateway_untouched(self):
        with tempfile.TemporaryDirectory() as directory:
            control = self.make_control(Path(directory))
            control._read_record = lambda path: None
            control._healthy = lambda project: True
            terminated = []
            control._terminate_record = lambda record: terminated.append(record)

            output = io.StringIO()
            with redirect_stdout(output):
                control.stop_project("gateway")

            self.assertEqual(terminated, [])
            self.assertIn("external service left running", output.getvalue())

    def test_status_reports_agent_as_embedded_in_gateway(self):
        with tempfile.TemporaryDirectory() as directory:
            control = self.make_control(Path(directory))
            with patch.object(
                control, "_read_record", return_value=None
            ), patch.object(control, "_healthy", return_value=True):
                status = control.project_status("agent")

            self.assertEqual(status.state, "external")
            self.assertEqual(status.detail, "embedded in gateway")

    def test_mem0_status_requires_complete_compose_service_group(self):
        with tempfile.TemporaryDirectory() as directory:
            control = self.make_control(Path(directory))
            control._healthy = lambda project: True
            control._compose_containers = lambda project: [
                {"State": "running", "Status": "Up"}
            ]

            status = control.project_status("mem0")

            self.assertEqual(status.state, "starting")

    def test_mem0_status_waits_for_container_health(self):
        with tempfile.TemporaryDirectory() as directory:
            control = self.make_control(Path(directory))
            control._healthy = lambda project: True
            control._compose_containers = lambda project: [
                {"State": "running", "Status": "Up", "HealthStatus": "healthy"},
                {"State": "running", "Status": "Up", "HealthStatus": "none"},
                {
                    "State": "running",
                    "Status": "Up (health: starting)",
                    "HealthStatus": "starting",
                },
            ]

            status = control.project_status("mem0")

            self.assertEqual(status.state, "starting")

    def test_compose_status_treats_exited_containers_as_stopped(self):
        with tempfile.TemporaryDirectory() as directory:
            control = self.make_control(Path(directory))
            control._healthy = lambda project: False
            control._compose_containers = lambda project: [
                {"State": "exited", "Status": "Exited (0)"}
            ]

            status = control.project_status("openkb")

            self.assertEqual(status.state, "stopped")


class EnvironmentTest(unittest.TestCase):
    def test_env_reader_preserves_quoted_spaces_and_dollar_characters(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text(
                "A='value with $ and spaces'\n"
                "B=plain # comment\n"
                "export C=third\n",
                encoding="utf-8",
            )

            self.assertEqual(
                read_env_file(path),
                {"A": "value with $ and spaces", "B": "plain", "C": "third"},
            )

    def test_variable_expansion_only_uses_known_braced_names(self):
        self.assertEqual(
            expand_braced_variables("${ROOT}/data/${UNKNOWN}", {"ROOT": "/tmp"}),
            "/tmp/data/${UNKNOWN}",
        )


class CommandInstallerTest(unittest.TestCase):
    def test_installer_creates_idempotent_links_for_public_commands(self):
        repo_root = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as directory:
            command_dir = Path(directory) / "bin"
            environment = {**os.environ, "ICARUS_BIN_DIR": str(command_dir)}

            for _ in range(2):
                subprocess.run(
                    [str(repo_root / "scripts/install-commands.sh")],
                    cwd=repo_root, env=environment, check=True,
                    stdout=subprocess.DEVNULL,
                )

            self.assertEqual(
                (command_dir / "icarus").resolve(),
                (repo_root / "bin/icarus").resolve(),
            )
            self.assertEqual(
                (command_dir / "icarus-gateway").resolve(),
                (repo_root / "bin/icarus-gateway").resolve(),
            )


if __name__ == "__main__":
    unittest.main()
