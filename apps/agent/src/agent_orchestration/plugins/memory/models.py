"""Stable Icarus memory models independent from Mem0 payloads."""

from dataclasses import dataclass, field
from typing import Any, Literal, Mapping


MemoryScope = Literal["global", "workspace"]


@dataclass(frozen=True)
class MemoryItem:
    ref: str
    content: str
    relevance: float | None
    scope: MemoryScope
    created_at: str | None = None
    updated_at: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "ref": self.ref,
            "content": self.content,
            "relevance": self.relevance,
            "scope": self.scope,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True)
class MemoryRecord:
    item: MemoryItem
    user_id: str
    agent_id: str
    run_id: str
    expiration_date: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MemoryHistoryItem:
    operation: str
    content: str | None
    occurred_at: str | None
    raw_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "operation": self.operation,
            "content": self.content,
            "occurred_at": self.occurred_at,
            "raw_id": self.raw_id,
        }


@dataclass(frozen=True)
class MemoryRecallResult:
    items: tuple[MemoryItem, ...]
    query: str
    truncated: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "items": [item.as_dict() for item in self.items],
            "truncated": self.truncated,
        }
