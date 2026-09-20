"""Session-scoped persistence for oversized Tool Result text."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import os
from pathlib import Path
import re
import tempfile


_SAFE_STEM = re.compile(r"[^A-Za-z0-9._-]+")


@dataclass(frozen=True)
class StoredToolResult:
    path: Path
    original_bytes: int
    saved_bytes: int
    complete: bool


class ToolResultStore:
    def __init__(self, root: Path, *, max_file_bytes: int) -> None:
        if max_file_bytes < 1:
            raise ValueError("max_file_bytes must be positive")
        self.root = root.expanduser().resolve()
        self.max_file_bytes = max_file_bytes

    def save(
        self, *, task_id: str, tool_call_id: str, content: str,
        source_complete: bool = True,
    ) -> StoredToolResult:
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.root.chmod(0o700)
        task_dir = self._task_dir(task_id)
        task_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        task_dir.chmod(0o700)
        data = content.encode("utf-8", errors="replace")
        if len(data) <= self.max_file_bytes:
            saved = data
        else:
            saved = data[: self.max_file_bytes].decode(
                "utf-8", errors="ignore"
            ).encode("utf-8")
        complete = source_complete and len(saved) == len(data)
        digest = sha256(
            tool_call_id.encode("utf-8", errors="replace") + b"\0" + data
        ).hexdigest()[:16]
        stem = _SAFE_STEM.sub("_", tool_call_id).strip("._-")
        stem = (stem or "tool-result")[:80]
        target = task_dir / f"{stem}-{digest}.txt"
        if target.exists():
            existing_size = target.stat().st_size
            return StoredToolResult(
                target,
                len(data),
                existing_size,
                source_complete and existing_size == len(data),
            )
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=task_dir, prefix=".tool-result-", delete=False
            ) as temporary:
                temporary.write(saved)
                temporary.flush()
                os.fsync(temporary.fileno())
                temporary_path = Path(temporary.name)
            temporary_path.chmod(0o600)
            temporary_path.replace(target)
            target.chmod(0o600)
            if target.stat().st_size != len(saved):
                target.unlink(missing_ok=True)
                raise OSError("Tool Result file size verification failed")
        except BaseException:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
            raise
        return StoredToolResult(target, len(data), len(saved), complete)

    def _task_dir(self, task_id: str) -> Path:
        if (
            not task_id
            or task_id in {".", ".."}
            or not re.fullmatch(r"[A-Za-z0-9._-]+", task_id)
        ):
            raise ValueError("task_id contains unsafe characters")
        return self.root / task_id
