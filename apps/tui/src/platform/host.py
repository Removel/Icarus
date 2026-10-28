"""Desktop-session detection and explicit WSL host interoperability."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


class ClipboardUnavailableError(RuntimeError):
    """A desktop clipboard capability is unavailable or an operation failed."""


_wsl_cache: bool | None = None


def is_wsl() -> bool:
    global _wsl_cache
    if _wsl_cache is None:
        try:
            _wsl_cache = "microsoft" in Path("/proc/version").read_text().lower()
        except OSError:
            _wsl_cache = False
    return _wsl_cache


def detect_linux_session() -> str:
    # Do not cache: desktop environment variables may change during a session.
    if os.environ.get("WAYLAND_DISPLAY"):
        return "wayland"
    if os.environ.get("DISPLAY"):
        return "x11"
    return "none"


def require_tool(name: str) -> str:
    resolved = shutil.which(name)
    if resolved is None:
        raise ClipboardUnavailableError(f"tool: install {name} for clipboard access")
    return resolved


def host_powershell_command(script: str, payload: bytes | str) -> tuple[list[str], bytes]:
    """Use STA PowerShell; fixed code via argv, UTF-8 data only via stdin."""
    if isinstance(payload, str):
        payload = payload.encode("utf-8")
    executable = shutil.which("powershell.exe")
    if executable is None:
        executable = "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"
    return [executable, "-NoProfile", "-NonInteractive", "-STA", "-Command", script], payload


def run_host(argv: list[str], *, stdin: bytes | None = None, timeout: float = 5):
    try:
        return subprocess.run(
            argv, input=stdin, capture_output=True, timeout=timeout, check=False
        )
    except subprocess.TimeoutExpired as error:
        raise ClipboardUnavailableError("timeout: Windows host did not respond") from error
    except OSError as error:
        raise ClipboardUnavailableError(
            "tool: Windows interop unavailable; enable WSL interop and PowerShell"
        ) from error


def windows_path_to_local(path: str) -> Path:
    """Let WSL resolve drive mappings and UNC paths, rather than replacing C:."""
    try:
        result = subprocess.run(
            ["wslpath", "-u", path], capture_output=True, text=True,
            timeout=3, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ClipboardUnavailableError("tool: wslpath could not resolve clipboard file") from error
    if result.returncode or not result.stdout.strip():
        raise ClipboardUnavailableError("unsupported: Windows file is not accessible in WSL")
    return Path(result.stdout.rstrip("\r\n"))
