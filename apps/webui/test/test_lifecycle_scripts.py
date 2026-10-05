"""Lifecycle tests for the WebUI native background process scripts."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys

REPO_ROOT = Path(__file__).resolve().parents[3]
APP_DIR = REPO_ROOT / "apps" / "webui"
PINNED_PNPM = (
    (APP_DIR / "package.json").read_text(encoding="utf-8")
    .split('"packageManager": "pnpm@', 1)[1]
    .split('"', 1)[0]
)
INSTALL_SCRIPT = APP_DIR / "scripts" / "install.sh"
START_SCRIPT = APP_DIR / "scripts" / "icarus_start.py"
ENVIRONMENT_MODULE = REPO_ROOT / "scripts" / "icarus" / "environment.py"
BASH = shutil.which("bash")


def write_executable(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)
    return path


def make_bin(
    directory: Path, *, node: bool = True, corepack: bool = True
) -> tuple[Path, Path]:
    binary_dir = directory / "bin"
    binary_dir.mkdir(parents=True, exist_ok=True)
    for name in ("dirname", "mktemp", "chmod", "rm", "bash"):
        host_binary = shutil.which(name)
        assert host_binary is not None
        (binary_dir / name).symlink_to(host_binary)
    if node:
        write_executable(
            binary_dir / "node",
            f"#!{BASH}\n"
            f"if [ \"$1\" = '-p' ]; then printf 'pnpm@{PINNED_PNPM}\\n'; fi\n"
            "exit 0\n",
        )
    log = directory / "corepack.log"
    if corepack:
        write_executable(
            binary_dir / "corepack",
            f"#!{BASH}\n"
            f"printf '%s\\n' \"$*\" >> '{log}'\n",
        )
    return binary_dir, log


def run_install(directory: Path, *args: str, node: bool = True, corepack: bool = True):
    binary_dir, log = make_bin(directory, node=node, corepack=corepack)
    completed = subprocess.run(
        [BASH, str(INSTALL_SCRIPT), *args],
        env={"PATH": str(binary_dir)},
        capture_output=True,
        text=True,
    )
    return completed, log


def test_install_requires_node(tmp_path):
    completed, _ = run_install(tmp_path, node=False)

    assert completed.returncode == 1
    assert "Node.js 22.12" in completed.stderr


def test_install_requires_corepack(tmp_path):
    completed, _ = run_install(tmp_path, corepack=False)

    assert completed.returncode == 1
    assert "Corepack" in completed.stderr


def test_install_rejects_unknown_arguments(tmp_path):
    completed, _ = run_install(tmp_path, "--bogus")

    assert completed.returncode == 2
    assert "Usage:" in completed.stderr


def test_install_builds_with_corepack_for_empty_and_dev_arguments(tmp_path):
    for argument in ("", "--dev"):
        directory = tmp_path / (argument or "default")
        directory.mkdir()
        completed, log = run_install(directory, *([argument] if argument else []))
        assert completed.returncode == 0, completed.stderr
        calls = log.read_text(encoding="utf-8").splitlines()
        assert calls == ["pnpm install --frozen-lockfile", "pnpm build"]


def make_launcher_tree(root: Path, *, dist: bool = True) -> Path:
    scripts_dir = root / "scripts" / "icarus"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ENVIRONMENT_MODULE, scripts_dir / "environment.py")
    app_scripts = root / "apps" / "webui" / "scripts"
    app_scripts.mkdir(parents=True, exist_ok=True)
    launcher = app_scripts / "icarus_start.py"
    shutil.copyfile(START_SCRIPT, launcher)
    if dist:
        build = root / "apps" / "webui" / "apps" / "shell" / "dist"
        build.mkdir(parents=True, exist_ok=True)
        (build / "index.html").write_text("<!doctype html>", encoding="utf-8")
    return launcher


def make_node_capture(root: Path, capture: Path) -> Path:
    binary_dir = root / "bin"
    binary_dir.mkdir(parents=True, exist_ok=True)
    write_executable(
        binary_dir / "node",
        f"#!{BASH}\n"
        "{\n"
        f"  printf 'argv:%s\\n' \"$*\"\n"
        f"  printf 'cwd:%s\\n' \"$PWD\"\n"
        f"  printf 'host:%s\\n' \"$ICARUS_WEBUI_HOST\"\n"
        f"  printf 'port:%s\\n' \"$ICARUS_WEBUI_PORT\"\n"
        f"  printf 'mem0:%s\\n' \"$ICARUS_MEM0_ENDPOINT\"\n"
        f"  printf 'openkb:%s\\n' \"$ICARUS_OPENKB_API_TOKEN\"\n"
        f"}} > '{capture}'\n",
    )
    return binary_dir


def run_launcher(root: Path, launcher: Path, *, capture: Path, extra_env=None):
    binary_dir = make_node_capture(root, capture)
    environment = {"PATH": str(binary_dir)}
    environment.update(extra_env or {})
    return subprocess.run(
        [sys.executable, str(launcher)],
        env=environment,
        capture_output=True,
        text=True,
    )


def test_launcher_requires_credentials_for_non_loopback_host(tmp_path):
    launcher = make_launcher_tree(tmp_path)
    (tmp_path / ".env").write_text(
        "ICARUS_WEBUI_HOST=0.0.0.0\n", encoding="utf-8"
    )

    completed = run_launcher(
        tmp_path, launcher, capture=tmp_path / "capture.env"
    )

    assert completed.returncode == 1
    assert "ICARUS_WEBUI_USER" in completed.stderr
    assert not (tmp_path / "capture.env").exists()


def test_launcher_requires_build_output(tmp_path):
    launcher = make_launcher_tree(tmp_path, dist=False)

    completed = run_launcher(
        tmp_path, launcher, capture=tmp_path / "capture.env"
    )

    assert completed.returncode == 1
    assert "icarus install webui" in completed.stderr


def test_launcher_execs_node_with_dotenv_and_process_override(tmp_path):
    launcher = make_launcher_tree(tmp_path)
    (tmp_path / ".env").write_text(
        "ICARUS_WEBUI_PORT=18080\n"
        "ICARUS_MEM0_ENDPOINT=http://127.0.0.1:9999\n"
        "ICARUS_OPENKB_API_TOKEN=from-dotenv\n",
        encoding="utf-8",
    )
    capture = tmp_path / "capture.env"

    completed = run_launcher(
        tmp_path,
        launcher,
        capture=capture,
        extra_env={"ICARUS_WEBUI_PORT": "19999"},
    )

    assert completed.returncode == 0, completed.stderr
    values = dict(
        line.split(":", 1)
        for line in capture.read_text(encoding="utf-8").splitlines()
    )
    assert values["argv"] == "server/index.mjs"
    assert values["cwd"] == str(tmp_path / "apps" / "webui")
    assert values["host"] == "127.0.0.1"
    assert values["port"] == "19999"
    assert values["mem0"] == "http://127.0.0.1:9999"
    assert values["openkb"] == "from-dotenv"


def test_install_help_prints_usage(tmp_path):
    launcher = make_launcher_tree(tmp_path, dist=False)

    completed = subprocess.run(
        [sys.executable, str(launcher), "--help"],
        env={"PATH": os.environ.get("PATH", "")},
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0
    assert "Usage:" in completed.stdout


def test_install_keeps_nested_pnpm_on_the_pinned_version(tmp_path):
    """`pnpm build` runs a script that calls bare `pnpm`; a foreign pnpm on PATH
    (for example a Windows install shadowing corepack) must not be used."""
    binary_dir, log = make_bin(tmp_path)
    write_executable(
        binary_dir / "corepack",
        f"#!{BASH}\n"
        f"printf '%s\\n' \"$*\" >> '{log}'\n"
        "if [ \"$1\" = pnpm ] && [ \"$2\" = build ]; then\n"
        "  pnpm --filter @icarus/shell build || exit 1\n"
        "fi\n",
    )
    write_executable(
        binary_dir / "pnpm",
        f"#!{BASH}\n"
        "if [ \"$1\" = '--version' ]; then printf '11.5.0\\n'; exit 0; fi\n"
        f"printf 'foreign pnpm\\n' >> '{log}'\nexit 1\n",
    )

    completed = subprocess.run(
        [BASH, str(INSTALL_SCRIPT)],
        env={"PATH": str(binary_dir)},
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    recorded = log.read_text()
    assert "pnpm --filter @icarus/shell build" in recorded
    assert "foreign pnpm" not in recorded


def test_install_accepts_a_system_pnpm_matching_the_pin(tmp_path):
    """A deployment may install pnpm normally (Linux and Windows both can); when
    its version matches package.json, Corepack is not required."""
    binary_dir, _ = make_bin(tmp_path, corepack=False)
    pin = (
        (REPO_ROOT / "apps" / "webui" / "package.json")
        .read_text(encoding="utf-8")
        .split('"packageManager": "pnpm@', 1)[1]
        .split('"', 1)[0]
    )
    log = tmp_path / "pnpm.log"
    write_executable(
        binary_dir / "pnpm",
        f"#!{BASH}\n"
        f"printf '%s\\n' \"$*\" >> '{log}'\n"
        f"if [ \"$1\" = '--version' ]; then printf '{pin}\\n'; fi\n",
    )

    completed = subprocess.run(
        [BASH, str(INSTALL_SCRIPT)],
        env={"PATH": str(binary_dir)},
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    recorded = log.read_text()
    assert "install --frozen-lockfile" in recorded
    assert "build" in recorded

