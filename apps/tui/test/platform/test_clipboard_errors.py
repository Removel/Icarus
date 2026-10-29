"""Native clipboard error categories remain actionable, not empty/tool aliases."""

import ctypes
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from apps.tui.src.platform import clipboard


def test_windows_unknown_nonempty_format_is_unsupported(monkeypatch):
    user = SimpleNamespace(
        OpenClipboard=Mock(return_value=1), CloseClipboard=Mock(),
        IsClipboardFormatAvailable=Mock(return_value=0),
        RegisterClipboardFormatW=Mock(return_value=49152),
        GetClipboardData=Mock(),
        CountClipboardFormats=Mock(return_value=1),
    )
    monkeypatch.setattr(clipboard.sys, "platform", "win32")
    monkeypatch.setattr(ctypes, "WinDLL", lambda *a, **kw: user, raising=False)
    with pytest.raises(clipboard.ClipboardUnavailableError, match="unsupported"):
        clipboard.read_clipboard_content()
    user.CountClipboardFormats.return_value = 0
    assert clipboard.read_clipboard_content().text == ""


def test_wsl_failed_write_preserves_busy_category(monkeypatch):
    monkeypatch.setattr(clipboard.sys, "platform", "linux")
    monkeypatch.setattr(clipboard, "is_wsl", lambda: True)
    monkeypatch.setattr(clipboard, "run_host", lambda *a, **kw: SimpleNamespace(
        returncode=1, stdout=json.dumps({"type": "error", "code": "busy"}).encode()))
    with pytest.raises(clipboard.ClipboardUnavailableError, match="busy"):
        clipboard.write_clipboard_text("text")
