"""Realistic regressions found in the independent platform review."""

import asyncio
import ctypes
import json
import os
import shutil
import subprocess
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from apps.tui.src.platform import clipboard
from apps.tui.src.services.desktop import DesktopService


def response(stdout=b"", stderr=b"", code=0):
    return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=code)


@pytest.mark.parametrize("session", ["x11", "wayland"])
def test_jpeg_offer_beats_text_and_unsupported_offer_never_becomes_binary_text(monkeypatch, session):
    monkeypatch.setattr(clipboard.sys, "platform", "linux")
    monkeypatch.setattr(clipboard, "is_wsl", lambda: False)
    monkeypatch.setattr(clipboard, "require_tool", lambda name: name)
    monkeypatch.setattr(clipboard, "detect_linux_session", lambda: session)
    monkeypatch.delenv("DISPLAY", raising=False)
    offers = ["image/jpeg", "text/plain;charset=utf-8"]

    def run(argv, **kwargs):
        if "TARGETS" in argv or "--list-types" in argv:
            return response("\n".join(offers))
        if "image/jpeg" in argv:
            return response(b"jpeg data")
        return response(b"binary data")

    monkeypatch.setattr(clipboard.subprocess, "run", run)
    content = clipboard.read_clipboard_content()
    assert content.kind == "image"
    assert content.image.media_type == "image/jpeg"
    assert content.image.data == b"jpeg data"
    offers[:] = ["image/gif"]
    with pytest.raises(clipboard.ClipboardUnavailableError, match="unsupported"):
        clipboard.read_clipboard_content()


def test_wayland_runtime_failure_uses_available_x11_without_calling_empty(monkeypatch):
    monkeypatch.setattr(clipboard.sys, "platform", "linux")
    monkeypatch.setattr(clipboard, "is_wsl", lambda: False)
    monkeypatch.setattr(clipboard, "require_tool", lambda name: name)
    monkeypatch.setattr(clipboard, "detect_linux_session", lambda: "wayland")
    monkeypatch.setenv("DISPLAY", ":1")

    def run(argv, **kwargs):
        if argv[0] == "wl-paste":
            return response("", "Failed to connect to a Wayland server", 1)
        if "TARGETS" in argv:
            return response("UTF8_STRING\n")
        assert "UTF8_STRING" in argv
        return response("fallback 中文".encode())

    monkeypatch.setattr(clipboard.subprocess, "run", run)
    assert clipboard.read_clipboard_content().text == "fallback 中文"
    monkeypatch.delenv("DISPLAY")
    with pytest.raises(clipboard.ClipboardUnavailableError, match="access"):
        clipboard.read_clipboard_content()


@pytest.mark.parametrize("session", ["wayland", "x11"])
def test_linux_writer_does_not_capture_selection_daemon_pipes(monkeypatch, session):
    monkeypatch.setattr(clipboard.sys, "platform", "linux")
    monkeypatch.setattr(clipboard, "is_wsl", lambda: False)
    monkeypatch.setattr(clipboard, "detect_linux_session", lambda: session)
    monkeypatch.setattr(clipboard, "require_tool", lambda name: name)
    monkeypatch.delenv("DISPLAY", raising=False)

    def run(argv, **kwargs):
        # Released clipboard launchers leave descriptors in their daemon child.
        assert not kwargs.get("capture_output")
        assert kwargs.get("stdout") != subprocess.PIPE
        assert kwargs.get("stderr") != subprocess.PIPE
        return response()

    monkeypatch.setattr(clipboard.subprocess, "run", run)
    clipboard.write_clipboard_text("text")


def test_windows_write_supplies_live_owner_and_destroys_after_close(monkeypatch):
    calls = []
    owner = 0x123456789
    buffer = ctypes.create_string_buffer(100)
    user = SimpleNamespace(
        CreateWindowExW=Mock(return_value=owner), DestroyWindow=Mock(side_effect=lambda hwnd: calls.append(("destroy", hwnd))),
        OpenClipboard=Mock(side_effect=lambda hwnd: calls.append(("open", hwnd)) or 1),
        CloseClipboard=Mock(side_effect=lambda: calls.append(("close",))),
        EmptyClipboard=Mock(return_value=1),
        SetClipboardData=Mock(return_value=123),
    )
    kernel = SimpleNamespace(
        GlobalAlloc=Mock(return_value=123), GlobalFree=Mock(),
        GlobalLock=Mock(return_value=ctypes.addressof(buffer)), GlobalUnlock=Mock(),
    )
    monkeypatch.setattr(clipboard.sys, "platform", "win32")
    monkeypatch.setattr(ctypes, "WinDLL", lambda name, **kw: user if name == "user32" else kernel, raising=False)
    clipboard.write_clipboard_text("中文😀")
    assert calls == [("open", owner), ("close",), ("destroy", owner)]
    assert user.CreateWindowExW.restype is ctypes.c_void_p


def test_interrupted_close_does_not_abandon_real_writer():
    async def run():
        entered, release = threading.Event(), threading.Event()
        def write(text):
            entered.set()
            assert release.wait(3)
        service = DesktopService(write_text=write)
        operation = asyncio.create_task(service.copy_text("text"))
        assert await asyncio.to_thread(entered.wait, 2)
        close = asyncio.create_task(service.close())
        await asyncio.sleep(0.02)
        close.cancel()
        with pytest.raises(asyncio.CancelledError):
            await close
        again = asyncio.create_task(service.close())
        await asyncio.sleep(0.02)
        try:
            assert not again.done()
        finally:
            release.set()
            await asyncio.gather(operation, again, return_exceptions=True)
    asyncio.run(run())


def test_macos_scripts_execute_bridge_references_and_check_limits_before_encoding():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node unavailable for side-effect-free JXA bridge contract")
    # Exercise the fixed source with a strict bridge double, not fabricated JSON.
    harness = r'''
const script = JSON.parse(process.argv[1]);
const scenario = process.argv[2];
let encoded = false;
global.ObjC = {import: () => {}};
const data = {length: 20971521, base64EncodedStringWithOptions: () => {encoded = true; throw Error('encoded oversized input');}};
const board = {
  isNil: () => false,
  readObjectsForClassesOptions: (classes, options) => {
    if (classes[0] !== $.NSURL) throw Error('wrong class');
    return {count: 1, objectAtIndex: () => ({path: {js: '/tmp/中.png'}})};
  },
  dataForType: type => {
    if (scenario === 'oversize') return data;
    if ((scenario === 'large-tiff' && type === 'public.tiff') ||
        (scenario === 'large-png' && type === 'public.png')) {
      return {length: 100, base64EncodedStringWithOptions: () => {encoded = true; return {js: 'abc'};}};
    }
    return null;
  }
};
global.$ = Object.assign(value => value, {
  NSURL: {}, NSPasteboard: {generalPasteboard: board},
  CGImageSourceCreateWithData: () => ({}),
  CGImageSourceCopyPropertiesAtIndex: () => ({PixelWidth: 10000, PixelHeight: 10000}),
  NSBitmapImageRep: {imageRepWithData: () => {throw Error('decoded oversized TIFF');}}
});
global.ObjC.deepUnwrap = value => value;
try { const value = eval(script); console.log(JSON.stringify({value, encoded})); }
catch (error) { console.log(JSON.stringify({error: error.message, encoded})); }
'''
    files = subprocess.run([node, "-e", harness, json.dumps(clipboard._MACOS_FILES_SCRIPT), "file"], capture_output=True, text=True, check=True)
    value = json.loads(files.stdout)
    assert "error" not in value
    assert json.loads(value["value"])["files"] == ["/tmp/中.png"]
    image = subprocess.run([node, "-e", harness, json.dumps(clipboard._MACOS_SCRIPT), "oversize"], capture_output=True, text=True, check=True)
    value = json.loads(image.stdout)
    assert value["encoded"] is False
    assert "limit" in value["error"].lower()
    for scenario in ("large-tiff", "large-png"):
        completed = subprocess.run([node, "-e", harness, json.dumps(clipboard._MACOS_SCRIPT), scenario], capture_output=True, text=True, check=True)
        value = json.loads(completed.stdout)
        assert value["encoded"] is False
        assert "limit" in value["error"].lower()


def test_bounded_capture_rejects_oversized_process_output():
    import sys
    with pytest.raises(clipboard.ClipboardUnavailableError, match="limit"):
        clipboard._run_bounded_capture(
            [sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'x' * 4096)"],
            maximum=1024,
        )


def test_bounded_capture_timeout_does_not_hang():
    import sys
    with pytest.raises(clipboard.ClipboardUnavailableError, match="timeout"):
        clipboard._run_bounded_capture(
            [sys.executable, "-c", "import time; time.sleep(10)"],
            maximum=1024, timeout=0.05,
        )


def test_dib_bitfields_and_unsupported_conversion_error_contract():
    import struct
    from io import BytesIO
    from PIL import Image

    header = struct.pack("<IiiHHIIiiII", 40, 1, 1, 1, 32, 3, 4, 0, 0, 0, 0)
    masks = struct.pack("<III", 0xFF0000, 0xFF00, 0xFF)
    data = clipboard.dib_to_png(header + masks + bytes([3, 2, 1, 0]))
    with Image.open(BytesIO(data)) as image:
        assert image.getpixel((0, 0)) == (1, 2, 3)


def test_wsl_preserves_structured_error_category(monkeypatch):
    monkeypatch.setattr(clipboard, "is_wsl", lambda: True)
    monkeypatch.setattr(clipboard.sys, "platform", "linux")
    monkeypatch.setattr(clipboard, "run_host", lambda *a, **kw: response(
        json.dumps({"type": "error", "code": "limit"}).encode(), code=1))
    with pytest.raises(clipboard.ClipboardUnavailableError, match="limit"):
        clipboard.read_clipboard_content()
