"""Image limits and legacy entry points share the typed clipboard backend."""

import struct
import zlib

import pytest

from apps.tui.src.platform import clipboard


from apps.tui.test.fixtures.clipboard_images import png


@pytest.mark.parametrize("data", [b"bad", b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff\x00"])
def test_corrupt_image_rejected(data):
    with pytest.raises(clipboard.ClipboardUnavailableError):
        clipboard.validate_clipboard_image(clipboard.ClipboardImage(data, "image/png", "png"))


def test_encoded_and_pixel_limits_rejected_before_decode():
    for data in [png(10000, 10000), b"x" * (20 * 1024 * 1024 + 1)]:
        with pytest.raises(clipboard.ClipboardUnavailableError, match="limit"):
            clipboard.validate_clipboard_image(clipboard.ClipboardImage(data, "image/png", "png"))


def test_content_type_is_detected_not_trusted():
    value = clipboard.validate_clipboard_image(clipboard.ClipboardImage(png(), "image/jpeg", "jpg"))
    assert value.media_type == "image/png"
    assert value.extension == "png"


def test_legacy_readers_use_typed_backend(monkeypatch):
    image = clipboard.ClipboardImage(png(), "image/png", "png")
    monkeypatch.setattr(clipboard, "read_clipboard_content", lambda: clipboard.ClipboardContent("image", image=image))
    assert clipboard.read_clipboard_image() == image
    assert clipboard.read_clipboard_text() is None
    monkeypatch.setattr(clipboard, "read_clipboard_content", lambda: clipboard.ClipboardContent("text", text="中文\n"))
    assert clipboard.read_clipboard_image() is None
    assert clipboard.read_clipboard_text() == "中文\n"
