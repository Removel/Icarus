import base64
import ctypes
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from apps.tui.src.platform import clipboard
from apps.tui.src.platform.clipboard import (
    ClipboardImage,
    ClipboardImageReadError,
)


def test非windows文本使用系统剪贴板(monkeypatch):
    monkeypatch.setattr(clipboard, "read_clipboard_content", lambda: ClipboardContent("text", text="系统文本"))
    assert clipboard.read_clipboard_text() == "系统文本"


@pytest.mark.parametrize("failure", [None, "open", "format", "data", "lock", "decode"])
def test_windows文本剪贴板读取并释放资源(monkeypatch, failure):
    buffer = ctypes.create_unicode_buffer("中文拼音 😀\r\n第二行\n")
    user32 = SimpleNamespace(
        OpenClipboard=Mock(return_value=failure != "open"),
        CloseClipboard=Mock(),
        IsClipboardFormatAvailable=Mock(side_effect=lambda fmt: fmt == 13 and failure != "format"),
        RegisterClipboardFormatW=Mock(return_value=49152),
        CountClipboardFormats=Mock(return_value=0),
        GetClipboardData=Mock(return_value=0 if failure == "data" else 123),
    )
    kernel32 = SimpleNamespace(
        GlobalLock=Mock(
            return_value=0 if failure == "lock" else ctypes.addressof(buffer)
        ),
        GlobalUnlock=Mock(),
    )
    monkeypatch.setattr(clipboard.sys, "platform", "win32")
    monkeypatch.setattr(
        ctypes,
        "WinDLL",
        lambda name, **kwargs: user32 if name == "user32" else kernel32,
        raising=False,
    )
    if failure == "decode":
        monkeypatch.setattr(
            ctypes, "wstring_at", Mock(side_effect=ValueError("decode"))
        )
    if failure in {"open", "data", "lock", "decode"}:
        with pytest.raises((ClipboardUnavailableError, ValueError)):
            clipboard.read_clipboard_text()
    else:
        assert clipboard.read_clipboard_text() == (
            "" if failure == "format" else "中文拼音 😀\n第二行\n"
        )
    assert user32.CloseClipboard.call_count == (0 if failure == "open" else 1)
    assert kernel32.GlobalUnlock.call_count == (1 if failure in {None, "decode"} else 0)


def test_read_clipboard_image使用统一内容分发(monkeypatch):
    expected = ClipboardImage(b"image", "image/png", "png")
    monkeypatch.setattr(clipboard, "read_clipboard_content", lambda: ClipboardContent("image", image=expected))
    assert clipboard.read_clipboard_image() == expected
    monkeypatch.setattr(clipboard, "read_clipboard_content", lambda: ClipboardContent("text", text="plain"))
    assert clipboard.read_clipboard_image() is None


def test_read_macos_clipboard_image解析固定脚本结果(monkeypatch):
    encoded = base64.b64encode(b"png-bytes").decode("ascii")
    captured = {}

    def run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return SimpleNamespace(
            returncode=0,
            stdout=(
                f'{{"media_type":"image/png","extension":"png","data":"{encoded}"}}'
            ),
            stderr="",
        )

    monkeypatch.setattr(clipboard, "_run_bounded_capture", run)

    result = clipboard._read_macos_clipboard_image()

    assert result == ClipboardImage(b"png-bytes", "image/png", "png")
    assert captured["command"][:4] == [
        "/usr/bin/osascript",
        "-l",
        "JavaScript",
        "-e",
    ]
    assert captured["kwargs"]["timeout"] == 3
    assert captured["kwargs"]["maximum"] > 20 * 1024 * 1024
    script = captured["command"][4]
    assert "board.isNil()" in script
    assert "public.tiff" in script
    assert "NSBitmapImageFileTypePNG" in script
    assert "media_type: 'image/png'" in script


def test_read_macos_clipboard_image拒绝未转换的tiff(monkeypatch):
    encoded = base64.b64encode(b"tiff-bytes").decode("ascii")
    monkeypatch.setattr(
        clipboard,
        "_run_bounded_capture",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout=(
                f'{{"media_type":"image/tiff","extension":"tiff","data":"{encoded}"}}'
            ),
            stderr="",
        ),
    )

    with pytest.raises(ClipboardImageReadError, match="unsupported"):
        clipboard._read_macos_clipboard_image()


def test_read_macos_clipboard_image没有图片返回none(monkeypatch):
    monkeypatch.setattr(
        clipboard,
        "_run_bounded_capture",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="\n", stderr=""),
    )
    assert clipboard._read_macos_clipboard_image() is None


def test_read_macos_clipboard_image超时转为领域错误(monkeypatch):
    def timeout(*args, **kwargs):
        raise clipboard.subprocess.TimeoutExpired(args[0], timeout=3)

    monkeypatch.setattr(clipboard, "_run_bounded_capture", timeout)

    with pytest.raises(ClipboardImageReadError):
        clipboard._read_macos_clipboard_image()


@pytest.mark.parametrize(
    "result",
    [
        SimpleNamespace(returncode=1, stdout="", stderr="failure"),
        SimpleNamespace(returncode=0, stdout="not-json", stderr=""),
        SimpleNamespace(
            returncode=0,
            stdout=('{"media_type":"image/png","extension":"png","data":"not base64"}'),
            stderr="",
        ),
        SimpleNamespace(
            returncode=0,
            stdout=('{"media_type":"image/png","extension":"jpg","data":"aW1hZ2U="}'),
            stderr="",
        ),
    ],
)
def test_read_macos_clipboard_image失败转为领域错误(monkeypatch, result):
    monkeypatch.setattr(clipboard, "_run_bounded_capture", lambda *args, **kwargs: result)
    with pytest.raises(ClipboardImageReadError):
        clipboard._read_macos_clipboard_image()


# ---------------------------------------------------------------------------
# ClipboardContent / read_clipboard_content / write_clipboard_text (Task 2)
# ---------------------------------------------------------------------------

import json as _json

from apps.tui.src.platform.clipboard import (
    ClipboardContent,
    ClipboardUnavailableError,
    read_clipboard_content,
    write_clipboard_text,
)


def _fake_run(stdout="", returncode=0):
    return lambda *args, **kwargs: SimpleNamespace(
        returncode=returncode, stdout=stdout, stderr=""
    )


def _patch_host(monkeypatch, handler):
    """Route host interop calls through a fake handler.

    clipboard.py binds run_host at import time, so patch it there.
    """
    monkeypatch.setattr(clipboard, "run_host", handler)


def test_read_content文件引用优先于图片和文本(monkeypatch):
    monkeypatch.setattr(clipboard, "require_tool", lambda name: name)
    # Single .png file reference wins over text and image representations.
    # WSL host helper reports file references via its JSON payload.
    monkeypatch.setattr(clipboard, "is_wsl", lambda: True)
    monkeypatch.setattr(clipboard, "windows_path_to_local", lambda path: __import__("pathlib").Path(path))
    _patch_host(
        monkeypatch,
        lambda argv, **kw: SimpleNamespace(
            returncode=0,
            stdout=_json.dumps({"type": "file", "files": ["/tmp/pic.png"]}).encode(),
            stderr=b"",
        ),
    )
    content = read_clipboard_content()
    assert content.kind == "file"
    assert content.files == (__import__("pathlib").Path("/tmp/pic.png"),)


def test_read_content多文件拒绝(monkeypatch):
    monkeypatch.setattr(clipboard, "is_wsl", lambda: True)
    _patch_host(
        monkeypatch,
        lambda argv, **kw: SimpleNamespace(
            returncode=0,
            stdout=_json.dumps(
                {"type": "file", "files": ["/tmp/a.png", "/tmp/b.png"]}
            ).encode(),
            stderr=b"",
        ),
    )
    with pytest.raises(ClipboardUnavailableError, match="multiple"):
        read_clipboard_content()


def test_read_content图片表示选择(monkeypatch):
    monkeypatch.setattr(clipboard, "detect_linux_session", lambda: "wayland")
    monkeypatch.setattr(clipboard, "require_tool", lambda name: name)
    # Same image in two MIME representations is one image; pick png.
    monkeypatch.setattr(clipboard.sys, "platform", "linux")
    monkeypatch.setattr(clipboard, "is_wsl", lambda: False)

    def run(*args, **kwargs):
        argv = args[0] if args else kwargs.get("args", [])
        if "--list-types" in argv:
            return SimpleNamespace(
                returncode=0,
                stdout="image/png\nimage/jpeg\n",
                stderr="",
            )
        return SimpleNamespace(returncode=0, stdout=b"png-bytes", stderr="")

    monkeypatch.setattr(clipboard.subprocess, "run", run)
    content = read_clipboard_content()
    assert content.kind == "image"
    assert content.image is not None
    assert content.image.media_type == "image/png"


def test_read_content纯文本(monkeypatch):
    monkeypatch.setattr(clipboard, "_run_bounded_capture", lambda *a, **kw: SimpleNamespace(returncode=0, stdout="", stderr=""))
    monkeypatch.setattr(clipboard.sys, "platform", "darwin")
    def run(argv, **kwargs):
        if argv == ["pbpaste"]:
            return SimpleNamespace(returncode=0, stdout="hello clipboard", stderr="")
        if argv[-1] == clipboard._MACOS_FILES_SCRIPT:
            return SimpleNamespace(returncode=0, stdout='{"files":[]}', stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(clipboard.subprocess, "run", run)
    content = read_clipboard_content()
    assert content.kind == "text"
    assert content.text == "hello clipboard"


def test_read_content空剪贴板与工具缺失区分(monkeypatch):
    monkeypatch.setattr(clipboard, "_run_bounded_capture", lambda *a, **kw: SimpleNamespace(returncode=0, stdout="", stderr=""))
    monkeypatch.setattr(clipboard.sys, "platform", "darwin")
    # Only pbpaste exit 1 is the empty-text result; format probes must succeed.
    def run(argv, **kwargs):
        if argv == ["pbpaste"]:
            return SimpleNamespace(returncode=1, stdout="", stderr="")
        if argv[-1] == clipboard._MACOS_FILES_SCRIPT:
            return SimpleNamespace(returncode=0, stdout='{"files":[]}', stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(clipboard.subprocess, "run", run)
    content = read_clipboard_content()
    assert content.kind == "text" and content.text == ""

    # Tool missing entirely.
    def missing(*args, **kwargs):
        raise FileNotFoundError("pbpaste")

    monkeypatch.setattr(clipboard.subprocess, "run", missing)
    with pytest.raises(ClipboardUnavailableError, match="tool"):
        read_clipboard_content()


def test_write_text各平台命令正确(monkeypatch):
    captured = {}

    def run(argv, input=None, **kwargs):
        captured["argv"] = argv
        captured["input"] = input
        captured["kwargs"] = kwargs
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(clipboard.subprocess, "run", run)
    # No real tool lookup in tests: the command assertions carry the contract.
    monkeypatch.setattr(clipboard, "require_tool", lambda name: name)
    monkeypatch.setattr(clipboard, "detect_linux_session", lambda: "wayland")

    monkeypatch.setattr(clipboard.sys, "platform", "darwin")
    write_clipboard_text("中文 hello")
    assert captured["argv"] == ["pbcopy"]
    assert captured["input"] == "中文 hello".encode("utf-8")

    monkeypatch.setattr(clipboard.sys, "platform", "linux")
    monkeypatch.setattr(clipboard, "is_wsl", lambda: False)
    monkeypatch.setattr(clipboard, "detect_linux_session", lambda: "wayland")
    write_clipboard_text("hello")
    assert captured["argv"] == ["wl-copy"]

    monkeypatch.setattr(clipboard, "detect_linux_session", lambda: "x11")
    write_clipboard_text("hello")
    assert captured["argv"] == ["xclip", "-selection", "clipboard"]

    # WSL routes through the host helper with stdin data.
    monkeypatch.setattr(clipboard, "is_wsl", lambda: True)
    host_captured = {}

    def fake_run_host(argv, *, stdin=None, timeout=5.0):
        host_captured["argv"] = argv
        host_captured["input"] = stdin
        return SimpleNamespace(returncode=0, stdout=b"OK", stderr=b"")

    _patch_host(monkeypatch, fake_run_host)
    write_clipboard_text("hello wsl")
    assert any("powershell" in part.lower() for part in host_captured["argv"])
    assert host_captured["input"] == b"WRIT" + "hello wsl".encode("utf-8")


def test_write_text失败不静默(monkeypatch):
    monkeypatch.setattr(clipboard.sys, "platform", "darwin")
    monkeypatch.setattr(
        clipboard.subprocess,
        "run",
        _fake_run(returncode=1),
    )
    with pytest.raises(ClipboardUnavailableError):
        write_clipboard_text("text")


def test_windows文本写入句柄释放(monkeypatch):
    """GlobalUnlock and CloseClipboard run on both success and error paths."""
    calls = {"unlock": 0, "close": 0}

    user32 = SimpleNamespace(
        OpenClipboard=Mock(return_value=True),
        CreateWindowExW=Mock(return_value=789), DestroyWindow=Mock(),
        EmptyClipboard=Mock(),
        SetClipboardData=Mock(return_value=123),
        CloseClipboard=Mock(side_effect=lambda: calls.__setitem__("close", calls["close"] + 1)),
    )
    kernel32 = SimpleNamespace(
        GlobalAlloc=Mock(return_value=456),
        GlobalFree=Mock(),
        GlobalLock=Mock(return_value=789),
        GlobalUnlock=Mock(side_effect=lambda h: calls.__setitem__("unlock", calls["unlock"] + 1)),
    )
    monkeypatch.setattr(clipboard.sys, "platform", "win32")
    monkeypatch.setattr(
        ctypes,
        "WinDLL",
        lambda name, **kwargs: user32 if name == "user32" else kernel32,
        raising=False,
    )
    # Never let the real memmove dereference the mocked pointer.
    moved = {}
    monkeypatch.setattr(
        ctypes,
        "memmove",
        lambda dst, src, n: moved.__setitem__("bytes", bytes(src)),
        raising=False,
    )
    write_clipboard_text("win text")
    assert moved["bytes"] == "win text".encode("utf-16-le") + b"\x00\x00"
    assert calls["unlock"] == 1
    assert calls["close"] == 1

    # Failure path: SetClipboardData returns 0 -> error, handles still released.
    calls = {"unlock": 0, "close": 0}
    user32.SetClipboardData = Mock(return_value=0)
    with pytest.raises(ClipboardUnavailableError):
        write_clipboard_text("win text")
    assert calls["unlock"] == 1
    assert calls["close"] == 1


def test图片dib转png():
    # Minimal BITMAPINFOHEADER DIB: 40-byte header, 2x3 px, 24bpp.
    import struct
    import zlib

    width, height = 2, 3
    header = struct.pack(
        "<IiiHHIIiiII",
        40,
        width,
        height,
        1,
        24,
        0,
        0,
        0,
        0,
        0,
        0,
    )
    row_size = ((width * 3 + 3) // 4) * 4
    row = (b"\x10\x20\x30" * width).ljust(row_size, b"\x00")
    pixels = row * height
    dib = header + pixels

    png = clipboard.dib_to_png(dib)

    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    # Parse IHDR to verify dimensions.
    ihdr_len = struct.unpack(">I", png[8:12])[0]
    assert ihdr_len == 13
    ihdr = png[16:29]
    w, h = struct.unpack(">II", ihdr[:8])
    assert (w, h) == (width, height)
    # IDAT zlib-decompresses to the raw pixel rows.
    idat_start = png.find(b"IDAT")
    assert idat_start > 0
    raw = zlib.decompress(png[idat_start + 4 : -8])
    assert len(raw) == (width * 3 + 1) * height

    with pytest.raises(Exception):
        clipboard.dib_to_png(b"\x00" * 5)
