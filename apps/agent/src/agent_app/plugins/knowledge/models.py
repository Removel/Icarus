"""Stable Icarus knowledge models independent from OpenKB payloads."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, BinaryIO


@dataclass(frozen=True)
class KnowledgeQueryResult:
    answer: str

    def as_dict(self) -> dict[str, Any]:
        return {"answer": self.answer}


@dataclass(frozen=True)
class KnowledgeDocument:
    ref: str
    name: str
    document_type: str
    display_type: str
    pages: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "ref": self.ref,
            "name": self.name,
            "document_type": self.document_type,
            "display_type": self.display_type,
            "pages": self.pages,
        }


@dataclass(frozen=True)
class KnowledgeCatalog:
    documents: tuple[KnowledgeDocument, ...]
    summaries: tuple[str, ...]
    concepts: tuple[str, ...]
    entities: tuple[str, ...]
    reports: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "documents": [item.as_dict() for item in self.documents],
            "document_count": len(self.documents),
            "summaries": list(self.summaries),
            "concepts": list(self.concepts),
            "entities": list(self.entities),
            "reports": list(self.reports),
        }


@dataclass(frozen=True)
class KnowledgePage:
    path: str
    content: str

    def as_dict(self) -> dict[str, Any]:
        return {"path": self.path, "content": self.content}


@dataclass(frozen=True)
class KnowledgeUploadSource:
    name: str
    stream: BinaryIO


@dataclass(frozen=True)
class KnowledgeUploadItem:
    name: str
    status: str
    message: str

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "status": self.status, "message": self.message}


@dataclass(frozen=True)
class KnowledgeUploadResult:
    files: tuple[KnowledgeUploadItem, ...]
    added_count: int
    skipped_count: int
    failed_count: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "files": [item.as_dict() for item in self.files],
            "added_count": self.added_count,
            "skipped_count": self.skipped_count,
            "failed_count": self.failed_count,
        }


@dataclass(frozen=True)
class KnowledgeRecompileDocument:
    name: str | None
    document: str | None
    document_type: str
    status: str
    elapsed_seconds: float | None = None
    message: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "document": self.document,
            "document_type": self.document_type,
            "status": self.status,
            "elapsed_seconds": self.elapsed_seconds,
            "message": self.message,
        }


@dataclass(frozen=True)
class KnowledgeRecompileResult:
    status: str
    total: int
    recompiled: int
    skipped: int
    documents: tuple[KnowledgeRecompileDocument, ...] = ()
    candidates: tuple[dict[str, str], ...] = ()
    message: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "total": self.total,
            "recompiled": self.recompiled,
            "skipped": self.skipped,
            "documents": [item.as_dict() for item in self.documents],
            "candidates": [dict(item) for item in self.candidates],
            "message": self.message,
        }
