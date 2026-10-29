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
    ClipboardUnavailableError,
    windows_path_to_local,
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
    """Compatibility entry point backed by the current system clipboard."""
    content = read_clipboard_content()
    return content.text if content.kind == "text" else None


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
ObjC.import('ImageIO');

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
      if (data.length > 20971520) throw new Error('limit: image exceeds 20 MiB');
      const source = $.CGImageSourceCreateWithData(data, null);
      if (!source) throw new Error('Unable to inspect clipboard image');
      const properties = ObjC.deepUnwrap($.CGImageSourceCopyPropertiesAtIndex(source, 0, null));
      const width = Number(properties.PixelWidth);
      const height = Number(properties.PixelHeight);
      if (!(width > 0 && height > 0) || width * height > 40000000) {
        throw new Error('limit: image exceeds 40 million pixels');
      }
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
    if (tiffData.length > 20971520) throw new Error('limit: TIFF exceeds 20 MiB');
    const source = $.CGImageSourceCreateWithData(tiffData, null);
    if (!source) throw new Error('Unable to inspect clipboard TIFF');
    const properties = ObjC.deepUnwrap($.CGImageSourceCopyPropertiesAtIndex(source, 0, null));
    const width = Number(properties.PixelWidth);
    const height = Number(properties.PixelHeight);
    if (!(width > 0 && height > 0) || width * height > 40000000) {
      throw new Error('limit: image exceeds 40 million pixels');
    }
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
    if (pngData.length > 20971520) throw new Error('limit: converted image exceeds 20 MiB');
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
    """Compatibility entry point using the typed backend, including files."""
    content = read_clipboard_content()
    if content.kind == "image":
        return content.image
    if content.kind == "file":
        return read_image_file(content.files[0])
    return None


def _read_macos_clipboard_image() -> ClipboardImage | None:
    try:
        result = _run_bounded_capture(
            [
                "/usr/bin/osascript",
                "-l",
                "JavaScript",
                "-e",
                _MACOS_SCRIPT,
            ],
            maximum=28 * 1024 * 1024 + 4096,
            timeout=3,
        )
    except (OSError, subprocess.TimeoutExpired, ClipboardUnavailableError) as error:
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


def validate_clipboard_image(image: ClipboardImage) -> ClipboardImage:
    """Validate actual PNG/JPEG bytes before a resource snapshot is adopted."""
    from io import BytesIO
    import warnings

    from PIL import Image, UnidentifiedImageError

    if len(image.data) > 20 * 1024 * 1024:
        raise ClipboardUnavailableError("limit: image exceeds 20 MiB")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(image.data)) as decoded:
                width, height = decoded.size
                if width <= 0 or height <= 0 or width * height > _MAX_PIXELS:
                    raise ClipboardUnavailableError("limit: image exceeds 40 million pixels")
                image_format = decoded.format
                if image_format not in {"PNG", "JPEG"}:
                    raise ClipboardUnavailableError("unsupported: use PNG or JPEG")
                decoded.verify()
            with Image.open(BytesIO(image.data)) as decoded:
                decoded.load()
    except ClipboardUnavailableError:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as error:
        raise ClipboardUnavailableError("limit: image exceeds safe pixel dimensions") from error
    except (OSError, ValueError, UnidentifiedImageError) as error:
        raise ClipboardUnavailableError("unsupported: corrupt image") from error
    media_type, extension = {"PNG": ("image/png", "png"), "JPEG": ("image/jpeg", "jpg")}[image_format]
    return ClipboardImage(image.data, media_type, extension)


def read_image_file(path: Path) -> ClipboardImage:
    try:
        with path.open("rb") as file:
            data = file.read(20 * 1024 * 1024 + 1)
    except OSError as error:
        raise ClipboardUnavailableError("access: clipboard image file is unavailable") from error
    return validate_clipboard_image(ClipboardImage(data, "", ""))


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


_MACOS_FILES_SCRIPT = r"""
ObjC.import('AppKit');
ObjC.import('Foundation');
(function () {
  const board = $.NSPasteboard.generalPasteboard;
  const urls = board.readObjectsForClassesOptions(
    [$.NSURL], $({'NSPasteboardURLReadingFileURLsOnlyKey': true})
  );
  const files = [];
  if (urls) {
    for (let i = 0; i < urls.count; i++) {
      files.push(urls.objectAtIndex(i).path.js);
    }
  }
  return JSON.stringify({type: 'file', files});
})()
"""


def _files_content(files) -> ClipboardContent:
    if len(files) > 1:
        raise ClipboardUnavailableError("multiple files on the clipboard")
    if not files:
        raise ClipboardUnavailableError("empty: file list is empty")
    return ClipboardContent(kind="file", files=tuple(Path(path) for path in files))


def _read_content_macos() -> ClipboardContent:
    result = _run_capture(
        ["/usr/bin/osascript", "-l", "JavaScript", "-e", _MACOS_FILES_SCRIPT],
        text=True,
    )
    if result.returncode:
        raise ClipboardUnavailableError("access: macOS pasteboard read failed")
    try:
        payload = json.loads(result.stdout)
        files = payload["files"]
        if not isinstance(files, list) or any(not isinstance(path, str) for path in files):
            raise ValueError("invalid file list")
    except (ValueError, KeyError, TypeError) as error:
        raise ClipboardUnavailableError("unsupported: invalid pasteboard file list") from error
    if files:
        return _files_content(files)
    try:
        image = _read_macos_clipboard_image()
    except ClipboardImageReadError as error:
        raise ClipboardUnavailableError("access: macOS image read failed") from error
    if image is not None:
        return ClipboardContent(kind="image", image=image)
    result = _run_capture(["pbpaste"], text=True)
    if result.returncode:
        if result.returncode == 1 and not result.stdout:
            return ClipboardContent(kind="text", text="")
        raise ClipboardUnavailableError("access: macOS text read failed")
    return ClipboardContent(kind="text", text=result.stdout.replace("\r\n", "\n"))


def _linux_sessions():
    import os

    session = detect_linux_session()
    if session == "none":
        raise ClipboardUnavailableError("tool: no Wayland or X11 session detected")
    yield session
    if session == "wayland" and os.environ.get("DISPLAY"):
        yield "x11"


def _read_content_linux() -> ClipboardContent:
    last_error = None
    for session in _linux_sessions():
        try:
            require_tool("wl-paste" if session == "wayland" else "xclip")
            return _linux_read(session)
        except ClipboardUnavailableError as error:
            if not str(error).startswith(("tool:", "access:", "timeout:")):
                raise
            last_error = error
    raise last_error


def _linux_read(session: str) -> ClipboardContent:
    wayland = session == "wayland"
    listing = _run_capture(
        ["wl-paste", "--list-types"] if wayland else ["xclip", "-selection", "clipboard", "-o", "-t", "TARGETS"],
        text=True,
    )
    if listing.returncode:
        diagnostic = listing.stderr
        if isinstance(diagnostic, bytes):
            diagnostic = diagnostic.decode("utf-8", errors="replace")
        if any(message in diagnostic.lower() for message in ("nothing is copied", "no selection", "target targets not available")):
            raise ClipboardUnavailableError("empty: clipboard has no selection")
        raise ClipboardUnavailableError("access: desktop clipboard connection failed")
    types = listing.stdout.splitlines()
    if not types:
        raise ClipboardUnavailableError("empty: clipboard offers no types")

    def read(media_type):
        argv = ["wl-paste", "--no-newline", "--type", media_type] if wayland else ["xclip", "-selection", "clipboard", "-o", "-t", media_type]
        raw = _run_capture(argv, text=False)
        if raw.returncode:
            raise ClipboardUnavailableError("access: clipboard representation read failed")
        return raw

    if "text/uri-list" in types:
        return _uri_list_content(read("text/uri-list"))
    for media_type, extension in (("image/png", "png"), ("image/jpeg", "jpg")):
        if media_type in types:
            raw = read(media_type)
            if not raw.stdout:
                raise ClipboardUnavailableError("empty: image representation unreadable")
            return ClipboardContent(kind="image", image=ClipboardImage(raw.stdout, media_type, extension))
    for text_type in ("text/plain;charset=utf-8", "UTF8_STRING", "text/plain", "TEXT", "STRING"):
        if text_type in types:
            data = read(text_type).stdout
            try:
                text = data.decode("latin-1" if text_type == "STRING" else "utf-8")
            except UnicodeError as error:
                raise ClipboardUnavailableError("unsupported: invalid clipboard text encoding") from error
            return ClipboardContent(kind="text", text=text.replace("\r\n", "\n"))
    raise ClipboardUnavailableError("unsupported: clipboard has no supported text or image")


def _uri_list_content(result) -> ClipboardContent:
    from urllib.parse import unquote, urlsplit

    if result.returncode:
        raise ClipboardUnavailableError("access: file clipboard read failed")
    try:
        text = result.stdout.decode("utf-8")
        references = [line for line in text.splitlines() if line and not line.startswith("#")]
        if len(references) > 1:
            raise ClipboardUnavailableError("multiple files on the clipboard")
        paths = []
        for reference in references:
            url = urlsplit(reference)
            if url.scheme != "file" or url.netloc not in {"", "localhost"}:
                raise ClipboardUnavailableError("unsupported: nonlocal clipboard file")
            path = unquote(url.path)
            if not path.startswith("/") or "\x00" in path:
                raise ValueError("invalid file URI")
            paths.append(Path(path))
        return _files_content(paths)
    except (ValueError, UnicodeError) as error:
        raise ClipboardUnavailableError("unsupported: invalid clipboard file URI") from error


def _run_bounded_capture(argv, *, maximum: int, timeout: float = 3):
    """Drain both pipes concurrently, killing before unbounded output is stored."""
    import threading

    buffers = [bytearray(), bytearray()]
    overflow = threading.Event()
    try:
        process = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except OSError as error:
        raise ClipboardUnavailableError("tool: desktop helper could not start") from error

    def drain(stream, buffer):
        with stream:
            while chunk := stream.read(65536):
                if len(buffer) + len(chunk) > maximum:
                    overflow.set()
                    process.kill()
                    break
                buffer.extend(chunk)

    readers = [threading.Thread(target=drain, args=(stream, buffer), daemon=True)
               for stream, buffer in zip((process.stdout, process.stderr), buffers)]
    for reader in readers:
        reader.start()
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired as error:
        process.kill()
        process.wait()
        raise ClipboardUnavailableError("timeout: desktop helper did not respond") from error
    finally:
        for reader in readers:
            reader.join()
    if overflow.is_set():
        raise ClipboardUnavailableError("limit: desktop helper response exceeds size limit")
    return subprocess.CompletedProcess(argv, process.returncode,
        buffers[0].decode("utf-8", errors="replace"), buffers[1].decode("utf-8", errors="replace"))


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
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[Console]::InputEncoding = New-Object System.Text.UTF8Encoding($false)
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$request = [Console]::In.ReadToEnd()
if ($request.Length -lt 4) { throw 'Invalid clipboard request' }
$mode = $request.Substring(0, 4)
$body = $request.Substring(4)
for ($attempt = 0; $attempt -lt 5; $attempt++) {
  try {
    if ($mode -eq 'WRIT') {
      [System.Windows.Forms.Clipboard]::SetText($body)
      [Console]::Write('OK')
      exit 0
    }
    if ($mode -ne 'READ') { throw 'Invalid clipboard mode' }
    $board = [System.Windows.Forms.Clipboard]::GetDataObject()
    if ($null -eq $board) {
      $response = @{type='text'; text=''}
    } elseif ($board.GetDataPresent([System.Windows.Forms.DataFormats]::FileDrop)) {
      $files = @($board.GetData([System.Windows.Forms.DataFormats]::FileDrop))
      $response = @{type='file'; files=$files}
    } elseif ($board.GetDataPresent('PNG')) {
      $stream = $board.GetData('PNG')
      try {
        if ($stream.Length -gt 20971520) { throw 'Image exceeds encoded size limit' }
        $bytes = $stream.ToArray()
        $response = @{type='image'; media_type='image/png'; extension='png'; data=[Convert]::ToBase64String($bytes)}
      } finally { $stream.Dispose() }
    } elseif ([System.Windows.Forms.Clipboard]::ContainsImage()) {
      $image = [System.Windows.Forms.Clipboard]::GetImage()
      $stream = New-Object System.IO.MemoryStream
      try {
        if ([long]$image.Width * [long]$image.Height -gt 40000000) { throw 'Image exceeds pixel limit' }
        $image.Save($stream, [System.Drawing.Imaging.ImageFormat]::Png)
        if ($stream.Length -gt 20971520) { throw 'Image exceeds encoded size limit' }
        $response = @{type='image'; media_type='image/png'; extension='png'; data=[Convert]::ToBase64String($stream.ToArray())}
      } finally { $stream.Dispose(); $image.Dispose() }
    } elseif ($board.GetDataPresent([System.Windows.Forms.DataFormats]::UnicodeText)) {
      $response = @{type='text'; text=[string]$board.GetData([System.Windows.Forms.DataFormats]::UnicodeText)}
    } else { throw 'Unsupported clipboard content' }
    [Console]::Write(($response | ConvertTo-Json -Depth 3 -Compress))
    exit 0
  } catch [System.Runtime.InteropServices.ExternalException] {
    if ($attempt -eq 4) {
      [Console]::Write('{"type":"error","code":"busy"}')
      exit 1
    }
    Start-Sleep -Milliseconds 10
  } catch {
    $code = if ($_.Exception.Message -like '*limit*') { 'limit' } elseif ($_.Exception.Message -like '*Unsupported*') { 'unsupported' } else { 'access' }
    [Console]::Write((@{type='error'; code=$code} | ConvertTo-Json -Compress))
    exit 1
  }
}
"""


def _read_content_wsl() -> ClipboardContent:
    payload = b"READ" + json.dumps({}).encode("utf-8")
    argv, stdin = host_powershell_command(_WIN_HELPER_SCRIPT, payload)
    result = run_host(argv, stdin=stdin)
    output = result.stdout.decode("utf-8", errors="replace").strip()
    try:
        payload_json = json.loads(output)
    except ValueError as error:
        raise ClipboardUnavailableError("access: invalid host clipboard response") from error
    if isinstance(payload_json, dict) and payload_json.get("type") == "error":
        code = payload_json.get("code")
        if code not in {"busy", "limit", "unsupported", "access", "empty"}:
            code = "access"
        raise ClipboardUnavailableError(f"{code}: Windows host clipboard request failed")
    if result.returncode:
        raise ClipboardUnavailableError("access: Windows host clipboard request failed")
    if not isinstance(payload_json, dict):
        raise ClipboardUnavailableError("unsupported: invalid host clipboard response")
    kind = payload_json.get("type")
    if kind == "file":
        files = payload_json.get("files")
        if not isinstance(files, list) or any(not isinstance(path, str) for path in files):
            raise ClipboardUnavailableError("unsupported: invalid host file list")
        content = _files_content(files)
        return ClipboardContent(kind="file", files=tuple(windows_path_to_local(str(path)) for path in content.files))
    if kind == "image":
        try:
            media_type = payload_json["media_type"]
            extension = payload_json["extension"]
            if {"image/png": "png", "image/jpeg": "jpg"}.get(media_type) != extension:
                raise ValueError("unsupported image representation")
            data = base64.b64decode(payload_json["data"], validate=True)
            if not data or len(data) > 20 * 1024 * 1024:
                raise ValueError("image exceeds encoded size limit")
        except (ValueError, KeyError, TypeError) as error:
            raise ClipboardUnavailableError("unsupported: invalid host image") from error
        return ClipboardContent(kind="image", image=ClipboardImage(data, media_type, extension))
    if kind == "text" and isinstance(payload_json.get("text"), str):
        return ClipboardContent(kind="text", text=payload_json["text"].replace("\r\n", "\n"))
    raise ClipboardUnavailableError("unsupported: host clipboard type")


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
            return _files_content(_win_read_files(user32))
        user32.RegisterClipboardFormatW.argtypes = [ctypes.wintypes.LPCWSTR]
        user32.RegisterClipboardFormatW.restype = ctypes.wintypes.UINT
        png_format = user32.RegisterClipboardFormatW("PNG")
        if png_format and user32.IsClipboardFormatAvailable(png_format):
            data = _win_read_format(user32, png_format)
            if data:
                return ClipboardContent(kind="image", image=ClipboardImage(data, "image/png", "png"))
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
        user32.CountClipboardFormats.argtypes = []
        user32.CountClipboardFormats.restype = ctypes.c_int
        if user32.CountClipboardFormats():
            raise ClipboardUnavailableError("unsupported: clipboard format is not text or image")
        return ClipboardContent(kind="text", text="")
    finally:
        user32.CloseClipboard()


def _win_open_clipboard(user32, owner=None) -> bool:
    for attempt in range(_WIN_RETRY_ATTEMPTS):
        if user32.OpenClipboard(owner):
            return True
        time.sleep(_WIN_RETRY_INTERVAL)
    return False


def _locked_kernel():
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalUnlock.restype = ctypes.c_int
    return kernel32


def _win_read_format(user32, fmt: int) -> bytes:
    handle = user32.GetClipboardData(fmt)
    if not handle:
        raise ClipboardUnavailableError("access: clipboard data unavailable")
    size = _win_global_size(handle)
    if not size or size > 160_000_124:
        raise ClipboardUnavailableError("unsupported: invalid clipboard allocation size")
    kernel32 = _locked_kernel()
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
    kernel32 = _locked_kernel()
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
    if count > 1:
        raise ClipboardUnavailableError("multiple files on the clipboard")
    files = []
    for index in range(count):
        length = shell32.DragQueryFileW(handle, index, None, 0)
        buffer = ctypes.create_unicode_buffer(length + 1)
        copied = shell32.DragQueryFileW(handle, index, buffer, length + 1)
        if copied != length:
            raise ClipboardUnavailableError("access: clipboard file path unavailable")
        files.append(Path(buffer.value))
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
            code = "access"
            try:
                error_payload = json.loads(result.stdout)
                if isinstance(error_payload, dict) and error_payload.get("code") in {"busy", "limit", "unsupported", "access"}:
                    code = error_payload["code"]
            except (ValueError, TypeError):
                pass
            raise ClipboardUnavailableError(f"{code}: host clipboard write failed")
        return
    if sys.platform == "linux":
        last_error = None
        for session in _linux_sessions():
            try:
                argv = [require_tool("wl-copy")] if session == "wayland" else [require_tool("xclip"), "-selection", "clipboard"]
                result = _run_write(argv, text.encode("utf-8"))
                if result.returncode:
                    raise ClipboardUnavailableError("access: desktop rejected clipboard write")
                return
            except ClipboardUnavailableError as error:
                last_error = error
        raise last_error
    raise ClipboardUnavailableError("tool: unsupported platform")


def _run_write(argv, stdin: bytes):
    try:
        return subprocess.run(
            argv, input=stdin, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=5, check=False
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

    kernel32.GlobalFree.argtypes = [wintypes.HANDLE]
    kernel32.GlobalFree.restype = wintypes.HANDLE
    data = text.encode("utf-16-le") + b"\x00\x00"
    handle = kernel32.GlobalAlloc(0x0002, len(data))  # GMEM_MOVEABLE
    if not handle:
        raise ClipboardUnavailableError("write: allocation failed")
    transferred = False
    opened = False
    owner = None
    user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR,
        wintypes.DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, ctypes.c_void_p]
    user32.CreateWindowExW.restype = ctypes.c_void_p
    user32.DestroyWindow.argtypes = [wintypes.HWND]
    user32.DestroyWindow.restype = wintypes.BOOL
    try:
        pointer = kernel32.GlobalLock(handle)
        if not pointer:
            raise ClipboardUnavailableError("write: lock failed")
        try:
            ctypes.memmove(pointer, data, len(data))
        finally:
            kernel32.GlobalUnlock(handle)
        # A thread-owned hidden STATIC window gives EmptyClipboard a valid
        # owner; NULL is safe for readers but makes subsequent writes fail.
        owner = user32.CreateWindowExW(0, "STATIC", "", 0, 0, 0, 0, 0, None, None, None, None)
        if not owner:
            raise ClipboardUnavailableError("write: clipboard owner window unavailable")
        opened = _win_open_clipboard(user32, owner)
        if not opened:
            raise ClipboardUnavailableError("busy: Windows clipboard is in use")
        if not user32.EmptyClipboard():
            raise ClipboardUnavailableError("write: unable to empty clipboard")
        if not user32.SetClipboardData(13, handle):  # CF_UNICODETEXT
            raise ClipboardUnavailableError("write: system rejected clipboard write")
        transferred = True
    finally:
        if opened:
            user32.CloseClipboard()
        if owner:
            user32.DestroyWindow(owner)
        if not transferred:
            kernel32.GlobalFree(handle)


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
    """Convert common DIB representations and normalize unsupported errors."""
    try:
        if len(dib) >= 20 and struct.unpack_from("<I", dib, 16)[0] == 3:
            from io import BytesIO
            from PIL import Image
            header_size, width, height = struct.unpack_from("<Iii", dib)
            if width <= 0 or height == 0 or width * abs(height) > _MAX_PIXELS:
                raise ValueError("image exceeds pixel limit")
            offset = 14 + header_size + (12 if header_size == 40 else 0)
            bmp = b"BM" + struct.pack("<IHHI", 14 + len(dib), 0, 0, offset) + dib
            with Image.open(BytesIO(bmp)) as image:
                output = BytesIO()
                image.convert("RGB").save(output, format="PNG")
                data = output.getvalue()
                if len(data) > 20 * 1024 * 1024:
                    raise ValueError("converted image exceeds encoded limit")
                return data
        return _rgb_dib_to_png(dib)
    except (ValueError, OSError, struct.error) as error:
        raise ClipboardUnavailableError("unsupported: invalid or unsupported DIB representation") from error


def _rgb_dib_to_png(dib: bytes) -> bytes:
    """Encode an uncompressed 24/32-bit DIB using stdlib only."""

    if len(dib) < 40:
        raise ValueError("DIB too small")
    header_size, width, height, planes, bpp = struct.unpack_from("<IiiHH", dib, 0)
    compression = struct.unpack_from("<I", dib, 16)[0]
    if header_size < 40 or header_size > len(dib) or width <= 0 or height == 0 or planes != 1 or compression != 0:
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
            b, g, r = row_bytes[column * bytes_pp : column * bytes_pp + 3]
            raw += bytes((r, g, b))

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    idat = zlib.compress(bytes(raw), 6)
    return PNG_SIGNATURE + _png_chunk(b"IHDR", ihdr) + _png_chunk(b"IDAT", idat) + _png_chunk(b"IEND", b"")
