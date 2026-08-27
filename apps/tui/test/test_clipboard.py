import base64
from types import SimpleNamespace

import pytest

from apps.tui.src import clipboard
from apps.tui.src.clipboard import (
    ClipboardImage,
    ClipboardImageReadError,
)


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
