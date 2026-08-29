"""Stable references to files staged in the local Runtime inbox."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import re


_SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9._-]+$")


class InvalidResourceError(ValueError):
    pass


class ResourceUnavailableError(FileNotFoundError):
    pass


@dataclass(frozen=True)
class ResourceRef:
    resource_id: str
    media_type: str | None = None

    def __post_init__(self) -> None:
        path = PurePosixPath(self.resource_id)
        if (
            not self.resource_id
            or path.is_absolute()
            or not path.parts
            or any(
                part in {"", ".", ".."} or not _SAFE_SEGMENT.fullmatch(part)
                for part in path.parts
            )
        ):
            raise InvalidResourceError("resource_id is invalid")

    def resolve(self, incoming_dir: Path) -> Path:
        root = incoming_dir.expanduser().resolve()
        target = (root / PurePosixPath(self.resource_id)).resolve()
        try:
            target.relative_to(root)
        except ValueError as error:
            raise InvalidResourceError(
                "resource_id escapes incoming directory"
            ) from error
        if not target.is_file():
            raise ResourceUnavailableError("resource is unavailable")
        return target
