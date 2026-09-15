"""Memory backend contracts used by MemoryPlugin."""

from typing import Any, Protocol

from apps.agent.src.agent_orchestration.plugins.memory.models import (
    MemoryHistoryItem,
    MemoryRecallResult,
    MemoryRecord,
    MemoryScope,
)


class MemoryReader(Protocol):
    async def arecall(
        self,
        query: str,
        *,
        workspace_key: str,
        scope: MemoryScope | None,
        include_stopped: bool,
        top_k: int,
        threshold: float,
    ) -> MemoryRecallResult: ...

    def recall(
        self,
        query: str,
        *,
        workspace_key: str,
        scope: MemoryScope | None,
        include_stopped: bool,
        top_k: int,
        threshold: float,
    ) -> MemoryRecallResult: ...

    def get(self, ref: str) -> MemoryRecord: ...

    def history(self, ref: str) -> tuple[MemoryHistoryItem, ...]: ...


class MemoryWriter(Protocol):
    def remember(
        self,
        content: str,
        *,
        workspace_key: str,
        scope: MemoryScope,
        metadata: dict[str, Any],
    ) -> tuple[MemoryRecord, ...]: ...

    def correct(self, ref: str, content: str) -> MemoryRecord: ...

    def set_expiration(self, ref: str, expiration_date: str | None) -> MemoryRecord: ...

    def delete(self, ref: str) -> None: ...


class MemoryBackend(MemoryReader, MemoryWriter, Protocol):
    def close(self) -> None: ...

    async def aclose(self) -> None: ...
