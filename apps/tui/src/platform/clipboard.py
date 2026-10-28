"""Platform-dispatched clipboard reading/writing for the TUI."""

import base64
import ctypes
import json
import struct
import subprocess
import sys
import time
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from apps.tui.src.platform.host import (
    detect_linux_session,
    host_powershell_command,
    is_wsl,
    require_tool,
    run_host,
)

_MAX_PIXELS = 40_000_000
_WIN_RETRY_ATTEMPTS = 5
_WIN_RETRY_INTERVAL = 0.01


def read_clipboard_text() -> str | None:
    """Read Windows Unicode text; None retains the Textual clipboard fallback."""
    if sys.platform != "win32":
        return None

    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32.OpenClipboard.argtypes = [wintypes.HWND]
    user32.OpenClipboard.restype = wintypes.BOOL
    user32.CloseClipboard.argtypes = []
    user32.CloseClipboard.restype = wintypes.BOOL
    user32.IsClipboardFormatAvailable.argtypes = [wintypes.UINT]
    user32.IsClipboardFormatAvailable.restype = wintypes.BOOL
    user32.GetClipboardData.argtypes = [wintypes.UINT]
    user32.GetClipboardData.restype = wintypes.HANDLE
    kernel32.GlobalLock.argtypes = [wintypes.HANDLE]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = [wintypes.HANDLE]
    kernel32.GlobalUnlock.restype = wintypes.BOOL

    if not user32.OpenClipboard(None):
        raise OSError("Windows clipboard is currently unavailable")
    try:
        if not user32.IsClipboardFormatAvailable(13):  # CF_UNICODETEXT
            return ""
        handle = user32.GetClipboardData(13)
        if not handle:
            raise OSError("Unable to read Windows clipboard text")
        pointer = kernel32.GlobalLock(handle)
        if not pointer:
            raise OSError("Unable to lock Windows clipboard text")
        try:
            return ctypes.wstring_at(pointer).replace("\r\n", "\n")
        finally:
            kernel32.GlobalUnlock(handle)
    finally:
        user32.CloseClipboard()


@dataclass(frozen=True)
class ClipboardImage:
    data: bytes
    media_type: str
    extension: str


class ClipboardImageReadError(RuntimeError):
    """The current platform supports image paste, but reading failed."""


_MACOS_SCRIPT = r"""
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
"""


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
        raise ClipboardImageReadError("macOS clipboard image could not be read")

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

    expected_extension = {"image/png": "png", "image/jpeg": "jpg"}.get(media_type)
    if expected_extension is None:
        raise ClipboardImageReadError(
            "macOS clipboard returned an unsupported image type"
        )
    if extension != expected_extension or not data:
        raise ClipboardImageReadError("macOS clipboard returned invalid image data")
    return ClipboardImage(data, media_type, extension)


class ClipboardUnavailableError(RuntimeError):
    """Platform capability missing, tool unavailable, timeout, or refused."""


@dataclass(frozen=True)
class ClipboardContent:
    kind: Literal["text", "image", "file"]
    text: str | None = None
    image: ClipboardImage | None = None
    files: tuple[Path, ...] = ()


# ---------------------------------------------------------------------------
# Content-typed read
# ---------------------------------------------------------------------------


def read_clipboard_content() -> ClipboardContent:
    """Read typed clipboard content, dispatching by platform (sync)."""

    if sys.platform == "darwin":
        return _read_content_macos()
    if sys.platform == "win32":
        return _read_content_windows()
    if is_wsl():
        return _read_content_wsl()
    if sys.platform == "linux":
        return _read_content_linux()
    raise ClipboardUnavailableError("tool: unsupported platform")


def _read_content_macos() -> ClipboardContent:
    image = None
    try:
        image = read_clipboard_image()
    except ClipboardImageReadError:
        pass
    if image is not None:
        return ClipboardContent(kind="image", image=image)
    try:
        result = subprocess.run(
            ["pbpaste"], capture_output=True, text=True, timeout=3, check=False
        )
    except FileNotFoundError as error:
        raise ClipboardUnavailableError("tool: pbpaste not found") from error
    except subprocess.TimeoutExpired as error:
        raise ClipboardUnavailableError("timeout: pbpaste timed out") from error
    if result.returncode != 0:
        return ClipboardContent(kind="text", text="")
    return ClipboardContent(kind="text", text=result.stdout.replace("\r\n", "\n"))


def _read_content_linux() -> ClipboardContent:
    session = detect_linux_session()
    if session == "wayland":
        return _read_content_linux_tool(
            lambda: require_tool("wl-paste"), _linux_wl_read
        )
    if session == "x11":
        return _read_content_linux_tool(
            lambda: require_tool("xclip"), _linux_x11_read
        )
    raise ClipboardUnavailableError("tool: no Wayland or X11 session detected")


def _read_content_linux_tool(require, reader) -> ClipboardContent:
    try:
        require()
    except ClipboardUnavailableError as error:
        raise ClipboardUnavailableError(f"tool: {error}") from error
    return reader()


def _linux_wl_read() -> ClipboardContent:
    listing = _run_capture(["wl-paste", "--list-types"], text=True)
    if listing.returncode != 0:
        return ClipboardContent(kind="text", text="")
    types = [line for line in listing.stdout.splitlines() if line]
    image_types = [t for t in types if t in {"image/png", "image/jpeg"}]
    if image_types:
        media_type = "image/png" if "image/png" in types else "image/jpeg"
        extension = "png" if media_type == "image/png" else "jpg"
        raw = _run_capture(["wl-paste", "-t", media_type], text=False)
        if raw.returncode != 0 or not raw.stdout:
            raise ClipboardUnavailableError("empty: image representation unreadable")
        return ClipboardContent(
            kind="image", image=ClipboardImage(raw.stdout, media_type, extension)
        )
    raw = _run_capture(["wl-paste", "-n"], text=False)
    if raw.returncode != 0:
        return ClipboardContent(kind="text", text="")
    return ClipboardContent(
        kind="text", text=raw.stdout.decode("utf-8", errors="replace").replace("\r\n", "\n")
    )


def _linux_x11_read() -> ClipboardContent:
    listing = _run_capture(["xclip", "-selection", "clipboard", "-o", "-t", "TARGETS"], text=True)
    if listing.returncode != 0:
        return ClipboardContent(kind="text", text="")
    types = [line for line in listing.stdout.splitlines() if line]
    if "image/png" in types:
        raw = _run_capture(["xclip", "-selection", "clipboard", "-o", "-t", "image/png"], text=False)
        if raw.returncode == 0 and raw.stdout:
            return ClipboardContent(
                kind="image", image=ClipboardImage(raw.stdout, "image/png", "png")
            )
    if "text/uri-list" in types and _x11_uris_are_files(types):
        return ClipboardContent(kind="file", files=())
    raw = _run_capture(["xclip", "-selection", "clipboard", "-o"], text=False)
    if raw.returncode != 0:
        return ClipboardContent(kind="text", text="")
    return ClipboardContent(
        kind="text", text=raw.stdout.decode("utf-8", errors="replace").replace("\r\n", "\n")
    )


def _x11_uris_are_files(_types: list[str]) -> bool:
    return False


def _run_capture(argv, *, text: bool):
    try:
        return subprocess.run(
            argv, capture_output=True, text=text, timeout=3, check=False
        )
    except FileNotFoundError as error:
        raise ClipboardUnavailableError(f"tool: {argv[0]} not found") from error
    except subprocess.TimeoutExpired as error:
        raise ClipboardUnavailableError(f"timeout: {argv[0]} timed out") from error


_WIN_HELPER_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$mode = [Console]::In.ReadToEnd().Substring(0, 4)
$raw = [Console]::OpenStandardInput()
$ms = New-Object System.IO.MemoryStream
$raw.CopyTo($ms)
$bytes = $ms.ToArray()
$body = [System.Text.Encoding]::UTF8.GetString($bytes, 4, $bytes.Length - 4)
if ($mode -eq 'READ') {
  $board = [System.Windows.Forms.Clipboard]::GetDataObject()
  if ($null -eq $board) { Write-Output '{"type":"none"}'; exit }
  if ($board.GetDataPresent('FileDrop')) {
    $files = @($board.GetData('FileDrop'))
    Write-Output (@{type='file'; files=$files} | ConvertTo-Json -Compress)
  } elseif ($board.GetDataPresent('PNG') -or $board.GetDataPresent('DeviceIndependentBitmap')) {
    Write-Output (@{type='image'} | ConvertTo-Json -Compress)
  } elseif ($board.GetDataPresent([System.Windows.Forms.DataFormats]::UnicodeText)) {
    Write-Output (@{type='text'; text=$board.GetData([System.Windows.Forms.DataFormats]::UnicodeText)} | ConvertTo-Json -Compress -InputObject (@{type='text'; text=[System.Text.Encoding]::UTF8.GetString([System.Text.Encoding]::UTF8.GetBytes($board.GetData([System.Windows.Forms.DataFormats]::UnicodeText)))}))
  } else {
    Write-Output '{"type":"none"}'
  }
} elseif ($mode -eq 'WRIT') {
  [System.Windows.Forms.Clipboard]::SetDataObject($body, $true)
  Write-Output 'OK'
} else {
  Write-Error 'unknown mode'
}
"""


def _read_content_wsl() -> ClipboardContent:
    payload = b"READ" + json.dumps({}).encode("utf-8")
    argv, stdin = host_powershell_command(_WIN_HELPER_SCRIPT, payload)
    result = run_host(argv, stdin=stdin)
    if result.returncode != 0:
        raise ClipboardUnavailableError("tool: WSL host clipboard read failed")
    output = result.stdout.decode("utf-8", errors="replace").strip()
    try:
        payload_json = json.loads(output)
    except ValueError as error:
        raise ClipboardUnavailableError("empty: host returned no content") from error
    kind = payload_json.get("type")
    if kind == "file":
        files = payload_json.get("files") or []
        if len(files) > 1:
            raise ClipboardUnavailableError("multiple files on the clipboard")
        return ClipboardContent(
            kind="file", files=tuple(Path(str(path)) for path in files)
        )
    if kind == "image":
        return _wsl_read_image()
    if kind == "text":
        return ClipboardContent(kind="text", text=str(payload_json.get("text", "")))
    return ClipboardContent(kind="text", text="")


def _wsl_read_image() -> ClipboardContent:
    raise ClipboardUnavailableError("tool: WSL image read requires the host helper")


def _read_content_windows() -> ClipboardContent:
    import ctypes.wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.OpenClipboard.argtypes = [ctypes.wintypes.HWND]
    user32.OpenClipboard.restype = ctypes.wintypes.BOOL
    user32.CloseClipboard.restype = ctypes.wintypes.BOOL
    user32.IsClipboardFormatAvailable.argtypes = [ctypes.wintypes.UINT]
    user32.IsClipboardFormatAvailable.restype = ctypes.wintypes.BOOL
    user32.GetClipboardData.argtypes = [ctypes.wintypes.UINT]
    user32.GetClipboardData.restype = ctypes.wintypes.HANDLE

    if not _win_open_clipboard(user32):
        raise ClipboardUnavailableError("busy: Windows clipboard is in use")
    try:
        if user32.IsClipboardFormatAvailable(15):  # CF_HDROP
            return ClipboardContent(kind="file", files=_win_read_files(user32))
        for fmt, media_type, extension in (
            (cf_png := 0, "image/png", "png"),
            (3, "image/jpeg", "jpg"),
        ):
            if cf_png and user32.IsClipboardFormatAvailable(fmt):
                data = _win_read_format(user32, fmt)
                if data:
                    return ClipboardContent(
                        kind="image", image=ClipboardImage(data, media_type, extension)
                    )
        if user32.IsClipboardFormatAvailable(8):  # DIB
            data = _win_read_format(user32, 8)
            if data:
                png = dib_to_png(data)
                return ClipboardContent(
                    kind="image", image=ClipboardImage(png, "image/png", "png")
                )
        if user32.IsClipboardFormatAvailable(13):  # CF_UNICODETEXT
            handle = user32.GetClipboardData(13)
            if not handle:
                raise ClipboardUnavailableError("empty: no clipboard text")
            text = _win_string_from_handle(handle)
            return ClipboardContent(kind="text", text=text)
        return ClipboardContent(kind="text", text="")
    finally:
        user32.CloseClipboard()


def _win_open_clipboard(user32) -> bool:
    for attempt in range(_WIN_RETRY_ATTEMPTS):
        if user32.OpenClipboard(None):
            return True
        time.sleep(_WIN_RETRY_INTERVAL)
    return False


def _win_read_format(user32, fmt: int) -> bytes:
    handle = user32.GetClipboardData(fmt)
    if not handle:
        return b""
    size = _win_global_size(handle)
    if not size:
        return b""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    pointer = kernel32.GlobalLock(handle)
    if not pointer:
        return b""
    try:
        return ctypes.string_at(pointer, size)
    finally:
        kernel32.GlobalUnlock(handle)


def _win_global_size(handle) -> int:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GlobalSize.argtypes = [ctypes.c_void_p]
    kernel32.GlobalSize.restype = ctypes.c_size_t
    return kernel32.GlobalSize(handle)


def _win_string_from_handle(handle) -> str:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    pointer = kernel32.GlobalLock(handle)
    if not pointer:
        raise ClipboardUnavailableError("empty: unable to lock clipboard text")
    try:
        return ctypes.wstring_at(pointer).replace("\r\n", "\n")
    finally:
        kernel32.GlobalUnlock(handle)


def _win_read_files(user32) -> tuple[Path, ...]:
    from ctypes import wintypes

    handle = user32.GetClipboardData(15)
    if not handle:
        return ()
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    shell32.DragQueryFileW.argtypes = [
        wintypes.HANDLE,
        wintypes.UINT,
        wintypes.LPWSTR,
        wintypes.UINT,
    ]
    shell32.DragQueryFileW.restype = wintypes.UINT
    count = shell32.DragQueryFileW(handle, 0xFFFFFFFF, None, 0)
    files = []
    buffer = ctypes.create_unicode_buffer(1024)
    for index in range(count):
        length = shell32.DragQueryFileW(handle, index, buffer, 1024)
        files.append(Path(buffer.value[:length]))
    return tuple(files)


# ---------------------------------------------------------------------------
# Text write
# ---------------------------------------------------------------------------


def write_clipboard_text(text: str) -> None:
    """Write text to the system clipboard; raise on any failure (sync)."""

    if sys.platform == "darwin":
        result = _run_write(["pbcopy"], text.encode("utf-8"))
        if result.returncode != 0:
            raise ClipboardUnavailableError("write: system rejected clipboard write")
        return
    if sys.platform == "win32":
        _write_text_windows(text)
        return
    if is_wsl():
        argv, stdin = host_powershell_command(_WIN_HELPER_SCRIPT, b"WRIT" + text.encode("utf-8"))
        result = run_host(argv, stdin=stdin)
        if result.returncode != 0 or result.stdout.strip() != b"OK":
            raise ClipboardUnavailableError("write: host clipboard write failed")
        return
    if sys.platform == "linux":
        session = detect_linux_session()
        if session == "wayland":
            argv = [require_tool("wl-copy")]
        elif session == "x11":
            argv = [require_tool("xclip"), "-selection", "clipboard"]
        else:
            raise ClipboardUnavailableError(
                "tool: no Wayland or X11 session detected"
            )
        result = _run_write(argv, text.encode("utf-8"))
        if result.returncode != 0:
            raise ClipboardUnavailableError("write: system rejected clipboard write")
        return
    raise ClipboardUnavailableError("tool: unsupported platform")


def _run_write(argv, stdin: bytes):
    try:
        return subprocess.run(
            argv, input=stdin, capture_output=True, timeout=5, check=False
        )
    except FileNotFoundError as error:
        raise ClipboardUnavailableError(f"tool: {argv[0]} not found") from error
    except subprocess.TimeoutExpired as error:
        raise ClipboardUnavailableError(f"timeout: {argv[0]} timed out") from error


def _write_text_windows(text: str) -> None:
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32.OpenClipboard.argtypes = [wintypes.HWND]
    user32.OpenClipboard.restype = wintypes.BOOL
    user32.EmptyClipboard.restype = wintypes.BOOL
    user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    user32.SetClipboardData.restype = wintypes.HANDLE
    user32.CloseClipboard.restype = wintypes.BOOL
    kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype = wintypes.HANDLE
    kernel32.GlobalLock.argtypes = [wintypes.HANDLE]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = [wintypes.HANDLE]
    kernel32.GlobalUnlock.restype = wintypes.BOOL

    GMEM_MOVEABLE = 0x0002
    if not _win_open_clipboard(user32):
        raise ClipboardUnavailableError("busy: Windows clipboard is in use")
    try:
        user32.EmptyClipboard()
        data = text.encode("utf-16-le") + b"\x00\x00"
        handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
        if not handle:
            raise ClipboardUnavailableError("write: allocation failed")
        pointer = kernel32.GlobalLock(handle)
        if not pointer:
            raise ClipboardUnavailableError("write: lock failed")
        try:
            ctypes.memmove(pointer, data, len(data))
        finally:
            kernel32.GlobalUnlock(handle)
        if not user32.SetClipboardData(13, handle):  # CF_UNICODETEXT
            kernel32.GlobalFree(handle) if hasattr(kernel32, "GlobalFree") else None
            raise ClipboardUnavailableError("write: system rejected clipboard write")
    finally:
        user32.CloseClipboard()


# ---------------------------------------------------------------------------
# DIB -> PNG (stdlib-only)
# ---------------------------------------------------------------------------

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _png_chunk(tag: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + tag
        + payload
        + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)
    )


def dib_to_png(dib: bytes) -> bytes:
    """Encode a BITMAPINFOHEADER DIB as PNG using stdlib zlib/struct only."""

    if len(dib) < 40:
        raise ValueError("DIB too small")
    header_size, width, height, planes, bpp = struct.unpack_from("<IiiHH", dib, 0)
    if header_size < 40 or width <= 0 or height == 0 or planes <= 0:
        raise ValueError("Unsupported DIB header")
    flip = height < 0
    height = abs(height)
    if width * height > _MAX_PIXELS:
        raise ValueError("image exceeds pixel limit")
    if bpp not in (24, 32):
        raise ValueError(f"Unsupported DIB depth: {bpp}")
    bytes_pp = bpp // 8
    row_size = (width * bytes_pp + 3) // 4 * 4
    pixel_data = dib[header_size:]
    if len(pixel_data) < row_size * height:
        raise ValueError("DIB pixel data truncated")

    # BITMAPINFO stores rows bottom-up (unless height is negative).
    raw = bytearray()
    for row in range(height):
        source_row = (height - 1 - row) if not flip else row
        offset = source_row * row_size
        raw.append(0)  # filter type 0 (None)
        row_bytes = pixel_data[offset : offset + width * bytes_pp]
        for column in range(width):
            b, g, r = row_bytes[column * 3 : column * 3 + 3]
            raw += bytes((r, g, b))

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    idat = zlib.compress(bytes(raw), 6)
    return PNG_SIGNATURE + _png_chunk(b"IHDR", ihdr) + _png_chunk(b"IDAT", idat) + _png_chunk(b"IEND", b"")
