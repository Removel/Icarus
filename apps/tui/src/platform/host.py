"""WSL detection and host interop command construction.

All desktop-environment/WSL probing stays in this module; callers never
branch on the platform themselves. Data travels through stdin/argv — the
host helper script text never embeds payloads (text, URLs, image names).
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

_wsl_cache: bool | None = None

PROC_VERSION = Path("/proc/version")


def is_wsl() -> bool:
    """True when running under WSL (kernel string reports microsoft)."""

    global _wsl_cache
    if _wsl_cache is not None:
        return _wsl_cache
    try:
        version = PROC_VERSION.read_text(encoding="utf-8", errors="replace")
    except OSError:
        _wsl_cache = False
        return _wsl_cache
    _wsl_cache = "microsoft" in version.lower()
    return _wsl_cache


def detect_linux_session() -> str:
    """Return "wayland", "x11", or "none" for the current Linux session."""

    import os

    if os.environ.get("WAYLAND_DISPLAY"):
        return "wayland"
    if os.environ.get("DISPLAY"):
        return "x11"
    return "none"


def _host_powershell_path() -> str:
    for name in (
        "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe",
        "/mnt/c/Windows/System32/cmd.exe",
    ):
        if Path(name).exists():
            if name.endswith("cmd.exe"):
                continue
            return name
    return "powershell.exe"


def host_powershell_command(script: str, payload: bytes | str) -> tuple[list[str], bytes]:
    """Build a WSL host interop command: script via argv, data via stdin."""

    if isinstance(payload, str):
        payload = payload.encode("utf-8")
    argv = [
        _host_powershell_path(),
        "-NoProfile",
        "-NonInteractive",
        "-Command",
        script,
    ]
    return argv, payload


def _unavailable(message: str):
    from apps.tui.src.platform.clipboard import ClipboardUnavailableError

    return ClipboardUnavailableError(message)


def run_host(argv: list[str], *, stdin: bytes | None = None, timeout: float = 5.0):
    """Run a host interop command, translating OS errors to domain errors."""

    try:
        return subprocess.run(
            argv,
            input=stdin,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as error:
        raise _unavailable(
            "tool: WSL host interop is unavailable"
        ) from error
    except subprocess.TimeoutExpired as error:
        raise _unavailable("timeout: host call timed out") from error
    except OSError as error:
        raise _unavailable(f"tool: host call failed: {error}") from error


def require_tool(name: str) -> str:
    """Resolve a tool path or raise a domain error naming the missing tool."""

    resolved = shutil.which(name)
    if resolved is None:
        raise _unavailable(f"tool: {name} is not installed")
    return resolved


def assert_not_windows_or_none() -> None:
    """No-op guard for readability; Windows never uses host interop."""

    if sys.platform == "win32":
        raise _unavailable("tool: windows uses native clipboard APIs")
