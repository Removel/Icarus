"""Focused tests for clipboard image lifecycle without a terminal."""

from __future__ import annotations

from pathlib import Path

from apps.tui.src.platform.clipboard import ClipboardImage
from apps.tui.src.services.clipboard_service import ClipboardService
from apps.tui.src.domain.submission import DraftImage, PendingMessage


def png_image(data: bytes = b"png-bytes") -> ClipboardImage:
    return ClipboardImage(data=data, media_type="image/png", extension="png")


def test_paste_image_stores_private_temporary_file(tmp_path: Path) -> None:
    service = ClipboardService(tmp_path, read_image=lambda: png_image())

    image = service.paste_image()

    assert image is not None
    assert image.owned_temporary_file is True
    assert image.path.exists()
    assert image.path.read_bytes() == b"png-bytes"
    assert image.path.stat().st_mode & 0o777 == 0o600
    service.cleanup()


def test_paste_returns_none_when_clipboard_has_no_image(tmp_path: Path) -> None:
    service = ClipboardService(tmp_path, read_image=lambda: None)

    assert service.paste_image() is None
    service.cleanup()


def test_resources_use_relative_ids_in_submission_order(tmp_path: Path) -> None:
    service = ClipboardService(tmp_path)
    first = DraftImage("image1", tmp_path / "first.png")
    second = DraftImage("image2", tmp_path / "second.png")

    resources = service.resources(
        PendingMessage("[#image2] [#image1]", (second, first))
    )

    assert [resource.resource_id for resource in resources] == [
        "second.png",
        "first.png",
    ]


def test_discard_removes_only_owned_temporary_files(tmp_path: Path) -> None:
    service = ClipboardService(tmp_path)
    owned = tmp_path / "owned.png"
    shared = tmp_path / "shared.png"
    owned.write_bytes(b"owned")
    shared.write_bytes(b"shared")

    service.discard(
        PendingMessage(
            "[#image1] [#image2]",
            (
                DraftImage("image1", owned, owned_temporary_file=True),
                DraftImage("image2", shared),
            ),
        )
    )

    assert owned.exists() is False
    assert shared.exists() is True


def test_cleanup_removes_temporary_directory(tmp_path: Path) -> None:
    service = ClipboardService(tmp_path, read_image=lambda: png_image())
    image = service.paste_image()
    assert image is not None
    directory = image.path.parent

    errors = service.cleanup()

    assert errors == []
    assert directory.exists() is False


def test_paste_failure_propagates_for_caller_notification(tmp_path: Path) -> None:
    def fail() -> ClipboardImage | None:
        raise OSError("clipboard unavailable")

    service = ClipboardService(tmp_path, read_image=fail)

    try:
        service.paste_image()
    except OSError as error:
        assert "clipboard unavailable" in str(error)
    else:
        raise AssertionError("paste failure must propagate")
