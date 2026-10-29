"""Typed desktop clipboard contracts; no real desktop calls."""

import base64
import ctypes
import json
import struct
import zlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from apps.tui.src.platform import clipboard, host


def result(value, *, binary=False, code=0):
    if isinstance(value, dict):
        value = json.dumps(value)
    if binary and isinstance(value, str):
        value = value.encode()
    return SimpleNamespace(returncode=code, stdout=value, stderr=b"" if binary else "")


@pytest.mark.parametrize("session", ["wayland", "x11"])
def test_linux_files_precede_image_representations(monkeypatch, session):
    monkeypatch.setattr(clipboard.sys, "platform", "linux")
    monkeypatch.setattr(clipboard, "is_wsl", lambda: False)
    monkeypatch.setattr(clipboard, "detect_linux_session", lambda: session)
    monkeypatch.setattr(clipboard, "require_tool", lambda name: name)
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        if "--list-types" in argv or "TARGETS" in argv:
            return result("image/png\ntext/uri-list\ntext/plain\n")
        assert "text/uri-list" in argv
        return result(b"# comment\r\nfile:///tmp/%E4%B8%AD%20image.png\r\n")

    monkeypatch.setattr(clipboard.subprocess, "run", run)
    content = clipboard.read_clipboard_content()
    assert content.kind == "file"
    assert content.files == (Path("/tmp/中 image.png"),)
    assert len(calls) == 2


@pytest.mark.parametrize("session", ["wayland", "x11"])
def test_linux_multifile_is_rejected(monkeypatch, session):
    monkeypatch.setattr(clipboard.sys, "platform", "linux")
    monkeypatch.setattr(clipboard, "is_wsl", lambda: False)
    monkeypatch.setattr(clipboard, "detect_linux_session", lambda: session)
    monkeypatch.setattr(clipboard, "require_tool", lambda name: name)
    monkeypatch.setattr(
        clipboard.subprocess,
        "run",
        lambda argv, **kw: result("text/uri-list\n")
        if "--list-types" in argv or "TARGETS" in argv
        else result(b"file:///tmp/a.png\nfile:///tmp/b.png\n"),
    )
    with pytest.raises(clipboard.ClipboardUnavailableError, match="multiple"):
        clipboard.read_clipboard_content()


def test_macos_file_reference_precedes_image(monkeypatch):
    monkeypatch.setattr(clipboard.sys, "platform", "darwin")
    monkeypatch.setattr(
        clipboard.subprocess, "run",
        lambda argv, **kw: result({"type": "file", "files": ["/tmp/a.png"]}),
    )
    content = clipboard.read_clipboard_content()
    assert content.kind == "file"
    assert content.files == (Path("/tmp/a.png"),)


def test_macos_read_error_is_not_empty_text(monkeypatch):
    monkeypatch.setattr(clipboard.sys, "platform", "darwin")
    monkeypatch.setattr(
        clipboard.subprocess, "run", lambda *a, **kw: result("", code=2),
    )
    with pytest.raises(clipboard.ClipboardUnavailableError):
        clipboard.read_clipboard_content()


def test_wsl_image_bytes_return_in_one_host_read(monkeypatch):
    monkeypatch.setattr(clipboard.sys, "platform", "linux")
    monkeypatch.setattr(clipboard, "is_wsl", lambda: True)
    data = b"png bytes"
    calls = []

    def run(argv, *, stdin):
        calls.append((argv, stdin))
        return result({"type": "image", "media_type": "image/png", "extension": "png",
                       "data": base64.b64encode(data).decode()}, binary=True)

    monkeypatch.setattr(clipboard, "run_host", run)
    content = clipboard.read_clipboard_content()
    assert content.image == clipboard.ClipboardImage(data, "image/png", "png")
    assert len(calls) == 1


def test_wsl_file_paths_use_wslpath_not_drive_substitution(monkeypatch):
    monkeypatch.setattr(clipboard.sys, "platform", "linux")
    monkeypatch.setattr(clipboard, "is_wsl", lambda: True)
    path = r"C:\Users\名字\a image.png"
    monkeypatch.setattr(clipboard, "run_host", lambda *a, **kw: result(
        {"type": "file", "files": [path]}, binary=True))
    calls = []

    def run(argv, **kw):
        calls.append(argv)
        return result("/mnt/c/Users/名字/a image.png\n")

    monkeypatch.setattr(host.subprocess, "run", run)
    assert clipboard.read_clipboard_content().files == (Path("/mnt/c/Users/名字/a image.png"),)
    assert calls == [["wslpath", "-u", path]]


def test_windows_registers_png_and_preserves_full_width_handles(monkeypatch):
    payload = ctypes.create_string_buffer(b"PNG DATA")
    user = SimpleNamespace(
        OpenClipboard=Mock(return_value=1), CloseClipboard=Mock(),
        RegisterClipboardFormatW=Mock(return_value=49152),
        IsClipboardFormatAvailable=Mock(side_effect=lambda fmt: fmt == 49152),
        GetClipboardData=Mock(return_value=0x123456789),
    )
    kernel = SimpleNamespace(
        GlobalSize=Mock(return_value=8),
        GlobalLock=Mock(return_value=ctypes.addressof(payload)), GlobalUnlock=Mock(),
    )
    monkeypatch.setattr(clipboard.sys, "platform", "win32")
    monkeypatch.setattr(ctypes, "WinDLL", lambda name, **kw: user if name == "user32" else kernel, raising=False)
    content = clipboard.read_clipboard_content()
    assert content.image.data == b"PNG DATA"
    assert kernel.GlobalLock.restype is ctypes.c_void_p
    user.RegisterClipboardFormatW.assert_called_once_with("PNG")
    kernel.GlobalUnlock.assert_called_once_with(0x123456789)
    user.CloseClipboard.assert_called_once()


@pytest.mark.parametrize("failure", ["lock", "empty", "set", None])
def test_windows_write_frees_untransferred_memory(monkeypatch, failure):
    user = SimpleNamespace(
        OpenClipboard=Mock(return_value=1), CloseClipboard=Mock(),
        CreateWindowExW=Mock(return_value=789), DestroyWindow=Mock(),
        EmptyClipboard=Mock(return_value=failure != "empty"),
        SetClipboardData=Mock(return_value=0 if failure == "set" else 0x123456789),
    )
    buffer = ctypes.create_string_buffer(100)
    kernel = SimpleNamespace(
        GlobalAlloc=Mock(return_value=0x123456789),
        GlobalLock=Mock(return_value=0 if failure == "lock" else ctypes.addressof(buffer)),
        GlobalUnlock=Mock(), GlobalFree=Mock(),
    )
    monkeypatch.setattr(clipboard.sys, "platform", "win32")
    monkeypatch.setattr(ctypes, "WinDLL", lambda name, **kw: user if name == "user32" else kernel, raising=False)
    if failure:
        with pytest.raises(clipboard.ClipboardUnavailableError):
            clipboard.write_clipboard_text("中文😀")
    else:
        clipboard.write_clipboard_text("中文😀")
    assert kernel.GlobalFree.call_count == (1 if failure else 0)
    # Allocation and lock precede opening, so lock failure needs no CloseClipboard.
    assert user.CloseClipboard.call_count == (0 if failure == "lock" else 1)


def test_dib_32bit_stride_is_per_pixel_and_topdown():
    header = struct.pack("<IiiHHIIiiII", 40, 2, -1, 1, 32, 0, 8, 0, 0, 0, 0)
    png = clipboard.dib_to_png(header + bytes([3, 2, 1, 0, 6, 5, 4, 0]))
    index = png.index(b"IDAT")
    size = struct.unpack_from(">I", png, index - 4)[0]
    raw = zlib.decompress(png[index + 4:index + 4 + size])
    assert raw == bytes([0, 1, 2, 3, 4, 5, 6])


@pytest.mark.parametrize("field,value", [(4, 0), (12, 2), (16, 1)])
def test_dib_rejects_invalid_geometry_planes_compression(field, value):
    header = bytearray(struct.pack("<IiiHHIIiiII", 40, 2, 1, 1, 24, 0, 8, 0, 0, 0, 0))
    struct.pack_into("<H" if field == 12 else "<I", header, field, value)
    with pytest.raises((ValueError, clipboard.ClipboardUnavailableError)):
        clipboard.dib_to_png(bytes(header) + b"\0" * 8)
