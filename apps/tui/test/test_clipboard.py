import base64
import ctypes
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from apps.tui.src import clipboard
from apps.tui.src.clipboard import (
    ClipboardImage,
    ClipboardImageReadError,
)


def test非windows文本使用内部剪贴板(monkeypatch):
    monkeypatch.setattr(clipboard.sys, "platform", "linux")
    assert clipboard.read_clipboard_text() is None


@pytest.mark.parametrize("failure", [None, "open", "format", "data", "lock", "decode"])
def test_windows文本剪贴板读取并释放资源(monkeypatch, failure):
    buffer = ctypes.create_unicode_buffer("中文拼音 😀\r\n第二行\n")
    user32 = SimpleNamespace(
        OpenClipboard=Mock(return_value=failure != "open"),
        CloseClipboard=Mock(),
        IsClipboardFormatAvailable=Mock(return_value=failure != "format"),
        GetClipboardData=Mock(return_value=0 if failure == "data" else 123),
    )
    kernel32 = SimpleNamespace(
        GlobalLock=Mock(return_value=0 if failure == "lock" else ctypes.addressof(buffer)),
        GlobalUnlock=Mock(),
    )
    monkeypatch.setattr(clipboard.sys, "platform", "win32")
    monkeypatch.setattr(
        ctypes, "WinDLL",
        lambda name, **kwargs: user32 if name == "user32" else kernel32,
        raising=False,
    )
    if failure == "decode":
        monkeypatch.setattr(ctypes, "wstring_at", Mock(side_effect=ValueError("decode")))
    if failure in {"open", "data", "lock", "decode"}:
        with pytest.raises((OSError, ValueError)):
            clipboard.read_clipboard_text()
    else:
        assert clipboard.read_clipboard_text() == (
            "" if failure == "format" else "中文拼音 😀\n第二行\n"
        )
    assert user32.CloseClipboard.call_count == (0 if failure == "open" else 1)
    assert kernel32.GlobalUnlock.call_count == (1 if failure in {None, "decode"} else 0)


def test_read_clipboard_image只在macos分发(monkeypatch):
    expected = ClipboardImage(b"image", "image/png", "png")
    monkeypatch.setattr(clipboard.sys, "platform", "darwin")
    monkeypatch.setattr(
        clipboard, "_read_macos_clipboard_image", lambda: expected
    )
    assert clipboard.read_clipboard_image() == expected

    monkeypatch.setattr(clipboard.sys, "platform", "linux")
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
                '{"media_type":"image/png",'
                '"extension":"png",'
                f'"data":"{encoded}"}}'
            ),
            stderr="",
        )

    monkeypatch.setattr(clipboard.subprocess, "run", run)

    result = clipboard._read_macos_clipboard_image()

    assert result == ClipboardImage(b"png-bytes", "image/png", "png")
    assert captured["command"][:4] == [
        "/usr/bin/osascript",
        "-l",
        "JavaScript",
        "-e",
    ]
    assert captured["kwargs"]["timeout"] == 3
    assert captured["kwargs"]["check"] is False
    script = captured["command"][4]
    assert "board.isNil()" in script
    assert "public.tiff" in script
    assert "NSBitmapImageFileTypePNG" in script
    assert "media_type: 'image/png'" in script


def test_read_macos_clipboard_image拒绝未转换的tiff(monkeypatch):
    encoded = base64.b64encode(b"tiff-bytes").decode("ascii")
    monkeypatch.setattr(
        clipboard.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout=(
                '{"media_type":"image/tiff",'
                f'"extension":"tiff","data":"{encoded}"}}'
            ),
            stderr="",
        ),
    )

    with pytest.raises(ClipboardImageReadError, match="unsupported"):
        clipboard._read_macos_clipboard_image()


def test_read_macos_clipboard_image没有图片返回none(monkeypatch):
    monkeypatch.setattr(
        clipboard.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0, stdout="\n", stderr=""
        ),
    )
    assert clipboard._read_macos_clipboard_image() is None


def test_read_macos_clipboard_image超时转为领域错误(monkeypatch):
    def timeout(*args, **kwargs):
        raise clipboard.subprocess.TimeoutExpired(args[0], timeout=3)

    monkeypatch.setattr(clipboard.subprocess, "run", timeout)

    with pytest.raises(ClipboardImageReadError):
        clipboard._read_macos_clipboard_image()


@pytest.mark.parametrize(
    "result",
    [
        SimpleNamespace(returncode=1, stdout="", stderr="failure"),
        SimpleNamespace(returncode=0, stdout="not-json", stderr=""),
        SimpleNamespace(
            returncode=0,
            stdout=(
                '{"media_type":"image/png",'
                '"extension":"png","data":"not base64"}'
            ),
            stderr="",
        ),
        SimpleNamespace(
            returncode=0,
            stdout=(
                '{"media_type":"image/png",'
                '"extension":"jpg","data":"aW1hZ2U="}'
            ),
            stderr="",
        ),
    ],
)
def test_read_macos_clipboard_image失败转为领域错误(monkeypatch, result):
    monkeypatch.setattr(
        clipboard.subprocess, "run", lambda *args, **kwargs: result
    )
    with pytest.raises(ClipboardImageReadError):
        clipboard._read_macos_clipboard_image()
