"""Validated HTTP(S) launch through the current desktop's URL association."""

from __future__ import annotations

import os
import subprocess
import sys
import unicodedata
from urllib.parse import urlsplit

from apps.tui.src.platform.host import host_powershell_command, is_wsl, run_host


def validate_http_url(url: str) -> str:
    if not isinstance(url, str) or not url:
        raise ValueError("URL is empty")
    if any(character.isspace() or unicodedata.category(character) == "Cc" for character in url):
        raise ValueError("URL contains whitespace or control characters")
    try:
        parts = urlsplit(url)
        if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
            raise ValueError("Only HTTP/HTTPS URLs with a host are allowed")
        parts.port  # Validate numeric range and malformed authority syntax.
    except ValueError as error:
        raise ValueError("Invalid HTTP/HTTPS URL") from error
    return url


_WSL_OPEN_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = New-Object System.Text.UTF8Encoding($false)
$url = [Console]::In.ReadToEnd()
Start-Process -FilePath $url
"""


def open_url(url: str) -> None:
    url = validate_http_url(url)
    if sys.platform == "win32":
        os.startfile(url)
        return
    if sys.platform == "linux" and is_wsl():
        # cmd /c start parses metacharacters even with shell=False in Python.
        # A fixed PowerShell script consumes URL data from stdin instead.
        argv, payload = host_powershell_command(_WSL_OPEN_SCRIPT, url)
        result = run_host(argv, stdin=payload)
    else:
        command = "open" if sys.platform == "darwin" else "xdg-open"
        result = subprocess.run(
            [command, url], capture_output=True, timeout=5, check=False
        )
    if result.returncode:
        raise OSError("Desktop rejected the browser request")
