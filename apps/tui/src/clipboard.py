"""Platform-dispatched clipboard image reading for the TUI."""

import base64
from dataclasses import dataclass
import subprocess
import sys


@dataclass(frozen=True)
class ClipboardImage:
    data: bytes
    media_type: str
    extension: str


class ClipboardImageReadError(RuntimeError):
    """The current platform supports image paste, but reading failed."""


_MACOS_SCRIPT = r'''
ObjC.import('AppKit');
ObjC.import('Foundation');

(function () {
  const board = $.NSPasteboard.generalPasteboard;
  if (board.isNil()) {
    return '';
  }
  const candidates = [
    ['public.png', 'image/png', 'png'],
    ['public.jpeg', 'image/jpeg', 'jpg'],
  ];

  for (const [typeName, mediaType, extension] of candidates) {
    const data = board.dataForType(typeName);
    if (data && data.length > 0) {
      const encoded = data.base64EncodedStringWithOptions(0).js;
      return JSON.stringify({
        media_type: mediaType,
        extension,
        data: encoded,
      });
    }
  }

  const tiffData = board.dataForType('public.tiff');
  if (tiffData && tiffData.length > 0) {
    const bitmap = $.NSBitmapImageRep.imageRepWithData(tiffData);
    if (!bitmap) {
      throw new Error('Unable to decode clipboard TIFF image');
    }
    const pngData = bitmap.representationUsingTypeProperties(
      $.NSBitmapImageFileTypePNG,
      $({})
    );
    if (!pngData || pngData.length === 0) {
      throw new Error('Unable to convert clipboard TIFF image to PNG');
    }
    const encoded = pngData.base64EncodedStringWithOptions(0).js;
    return JSON.stringify({
      media_type: 'image/png',
      extension: 'png',
      data: encoded,
    });
  }

  return '';
})()
'''


def read_clipboard_image() -> ClipboardImage | None:
    """Read one image from the current platform clipboard if supported."""

    if sys.platform == "darwin":
        return _read_macos_clipboard_image()
    return None


def _read_macos_clipboard_image() -> ClipboardImage | None:
    try:
        result = subprocess.run(
            [
                "/usr/bin/osascript",
                "-l",
                "JavaScript",
                "-e",
                _MACOS_SCRIPT,
            ],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ClipboardImageReadError(
            "macOS clipboard image could not be read"
        ) from error

    if result.returncode != 0:
        raise ClipboardImageReadError(
            "macOS clipboard image could not be read"
        )

    output = result.stdout.strip()
    if not output:
        return None

    try:
        import json

        payload = json.loads(output)
        media_type = str(payload["media_type"])
        extension = str(payload["extension"])
        data = base64.b64decode(payload["data"], validate=True)
    except (KeyError, TypeError, ValueError) as error:
        raise ClipboardImageReadError(
            "macOS clipboard returned invalid image data"
        ) from error

    expected_extension = {"image/png": "png", "image/jpeg": "jpg"}.get(
        media_type
    )
    if expected_extension is None:
        raise ClipboardImageReadError(
            "macOS clipboard returned an unsupported image type"
        )
    if extension != expected_extension or not data:
        raise ClipboardImageReadError(
            "macOS clipboard returned invalid image data"
        )
    return ClipboardImage(data, media_type, extension)
