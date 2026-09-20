"""KnowledgePlugin operations and workspace upload policy."""

from __future__ import annotations

from contextlib import ExitStack
import os
from pathlib import Path
import stat

from apps.agent.src.agent_orchestration.events import Event
from apps.agent.src.agent_orchestration.plugin_runtime import BasePlugin
from apps.agent.src.agent_orchestration.plugins.knowledge.backend import KnowledgeBackend
from apps.agent.src.agent_orchestration.plugins.knowledge.models import (
    KnowledgeUploadSource,
)
from apps.agent.src.agent_orchestration.tools.pagination import (
    page_number,
    page_size as validate_page_size,
    slice_page,
)


SUPPORTED_EXTENSIONS = frozenset(
    {
        ".pdf", ".md", ".markdown", ".docx", ".pptx",
        ".xlsx", ".xls", ".html", ".htm", ".txt", ".csv",
    }
)
DEFAULT_MAX_FILE_BYTES = 100 * 1024 * 1024
DEFAULT_MAX_REQUEST_BYTES = 500 * 1024 * 1024


class KnowledgeOperationError(RuntimeError):
    pass


class KnowledgePlugin(BasePlugin):
    def __init__(
        self,
        plugin_id: str,
        backend: KnowledgeBackend,
        *,
        workspace_path: Path,
        max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
        max_request_bytes: int = DEFAULT_MAX_REQUEST_BYTES,
        default_page_size: int = 50,
        max_page_size: int = 200,
    ) -> None:
        super().__init__(plugin_id)
        self.backend = backend
        self.workspace_path = workspace_path.expanduser().resolve()
        if max_file_bytes < 1:
            raise ValueError("knowledge max_file_bytes must be positive")
        if max_request_bytes < max_file_bytes:
            raise ValueError(
                "knowledge max_request_bytes must be at least max_file_bytes"
            )
        self.max_file_bytes = max_file_bytes
        self.max_request_bytes = max_request_bytes
        self.default_page_size = validate_page_size(
            default_page_size, maximum=max_page_size
        )
        self.max_page_size = validate_page_size(
            max_page_size, maximum=max_page_size
        )

    def accepts_event(self, source_plugin_id: str, event: Event) -> bool:
        del source_plugin_id, event
        return False

    async def consume(self, source_plugin_id: str, event: Event) -> None:
        del source_plugin_id, event

    async def stop(self) -> None:
        self.backend.close()

    def query(self, question: str) -> dict:
        return self.backend.query(question).as_dict()

    def list(self, *, page_num: int = 1, page_size: int | None = None) -> dict:
        page_num = page_number(page_num)
        page_size = validate_page_size(
            page_size,
            default=self.default_page_size,
            maximum=self.max_page_size,
        )
        catalog = self.backend.list().as_dict()
        collection_names = (
            "documents",
            "summaries",
            "concepts",
            "entities",
            "reports",
        )
        totals: dict[str, int] = {}
        combined: list[tuple[str, object]] = []
        for name in collection_names:
            values = catalog[name]
            totals[name] = len(values)
            combined.extend((name, value) for value in values)
            catalog[name] = []
        visible, has_more = slice_page(
            combined, page_num=page_num, page_size=page_size
        )
        for name, value in visible:
            catalog[name].append(value)
        catalog["pagination"] = {
            "page_num": page_num,
            "page_size": page_size,
            "has_more": has_more,
            "next_page_num": page_num + 1 if has_more else None,
            "total_count": len(combined),
            "totals": totals,
        }
        return catalog

    def read(self, path: str) -> dict:
        return self.backend.read(path).as_dict()

    def upload(self, paths: tuple[str, ...]) -> dict:
        if not paths:
            raise KnowledgeOperationError("paths must contain at least one file")
        with ExitStack() as stack:
            sources: list[KnowledgeUploadSource] = []
            total_bytes = 0
            directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
            root_fd = os.open(self.workspace_path, directory_flags)
            stack.callback(os.close, root_fd)
            for raw_path in paths:
                relative = self._relative_upload_path(raw_path)
                stream = stack.enter_context(
                    _open_workspace_file(root_fd, relative)
                )
                file_stat = os.fstat(stream.fileno())
                if not stat.S_ISREG(file_stat.st_mode):
                    raise KnowledgeOperationError(
                        f"upload path must be a regular file: {raw_path}"
                    )
                if file_stat.st_size > self.max_file_bytes:
                    raise KnowledgeOperationError(
                        f"upload file exceeds {self.max_file_bytes} bytes: {raw_path}"
                    )
                total_bytes += file_stat.st_size
                if total_bytes > self.max_request_bytes:
                    raise KnowledgeOperationError(
                        f"upload request exceeds {self.max_request_bytes} bytes"
                    )
                sources.append(KnowledgeUploadSource(relative.name, stream))
            return self.backend.upload(tuple(sources)).as_dict()

    def recompile(
        self,
        *,
        document: str | None,
        all_documents: bool,
        refresh_schema: bool,
    ) -> dict:
        if (document is None) == (not all_documents):
            raise KnowledgeOperationError(
                "exactly one of document or all_documents=true is required"
            )
        return self.backend.recompile(
            document=document,
            all_documents=all_documents,
            refresh_schema=refresh_schema,
        ).as_dict()

    def _relative_upload_path(self, raw_path: str) -> Path:
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise KnowledgeOperationError("each upload path must be a non-empty string")
        candidate = Path(raw_path).expanduser()
        if not candidate.is_absolute():
            candidate = self.workspace_path / candidate
        path = candidate.resolve()
        if not path.is_relative_to(self.workspace_path):
            raise KnowledgeOperationError(
                f"upload path is outside the current workspace: {raw_path}"
            )
        if not path.is_file():
            raise KnowledgeOperationError(
                f"upload path must be a regular file: {raw_path}"
            )
        if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            raise KnowledgeOperationError(
                f"unsupported upload file type: {path.suffix.lower() or '<none>'}"
            )
        return path.relative_to(self.workspace_path)


def _open_workspace_file(root_fd: int, relative: Path):
    """Open a resolved workspace path without following a raced symlink."""

    if not hasattr(os, "O_NOFOLLOW"):
        raise KnowledgeOperationError(
            "secure workspace upload is not supported on this platform"
        )
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | os.O_NOFOLLOW
    directory_flags = flags | getattr(os, "O_DIRECTORY", 0)
    current_fd = os.dup(root_fd)
    try:
        for part in relative.parts[:-1]:
            next_fd = os.open(part, directory_flags, dir_fd=current_fd)
            os.close(current_fd)
            current_fd = next_fd
        file_fd = os.open(relative.name, flags, dir_fd=current_fd)
    except OSError as error:
        raise KnowledgeOperationError(
            f"upload path changed or is not a safe regular file: {relative}"
        ) from error
    finally:
        os.close(current_fd)
    return os.fdopen(file_fd, "rb")
