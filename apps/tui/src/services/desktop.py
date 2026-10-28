"""Desktop I/O service: system clipboard writes and validated URL opening."""

from __future__ import annotations

import asyncio
from typing import Callable

from apps.tui.src.platform.clipboard import write_clipboard_text

_HTTP_SCHEMES = ("http://", "https://")


def validate_http_url(url: str) -> str:
    """Return the URL if it is a structurally valid http(s) target; else raise."""

    if not isinstance(url, str) or not url:
        raise ValueError("URL is empty")
    if any(character in url for character in "\x00\n\r\t") or any(
        ord(character) < 0x20 for character in url
    ):
        raise ValueError("URL contains control characters")
    stripped = url.strip()
    if not stripped or any(character.isspace() for character in stripped):
        raise ValueError("URL contains whitespace")
    if not stripped.lower().startswith(_HTTP_SCHEMES):
        raise ValueError(f"URL scheme not allowed: {stripped.split(':', 1)[0][:20]}")
    remainder = stripped[len(stripped.split("://", 1)[0]) + 3 :]
    if not remainder or remainder.startswith("/"):
        raise ValueError("URL has no host")
    if "//" in remainder:
        raise ValueError("URL has empty host segment")
    return stripped


class DesktopService:
    """Runs clipboard writes and URL opens off the Textual message loop."""

    def __init__(
        self,
        *,
        write_text: Callable[[str], None] = write_clipboard_text,
        open_url: Callable[[str], None] | None = None,
    ):
        self._write_text = write_text
        self._open_url = open_url

    async def copy_text(self, text: str) -> None:
        """Write text to the system clipboard in a worker thread."""

        await asyncio.to_thread(self._write_text, text)

    async def open_link(self, url: str) -> None:
        """Validate and open the URL through the injected opener."""

        validated = validate_http_url(url)
        if self._open_url is None:
            raise ValueError("no URL opener is wired")
        await asyncio.to_thread(self._open_url, validated)
