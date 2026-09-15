"""Knowledge backend contracts used by KnowledgePlugin."""

from typing import Protocol

from apps.agent.src.agent_orchestration.plugins.knowledge.models import (
    KnowledgeCatalog,
    KnowledgePage,
    KnowledgeQueryResult,
    KnowledgeRecompileResult,
    KnowledgeUploadResult,
    KnowledgeUploadSource,
)


class KnowledgeReader(Protocol):
    def query(self, question: str) -> KnowledgeQueryResult: ...

    def list(self) -> KnowledgeCatalog: ...

    def read(self, path: str) -> KnowledgePage: ...


class KnowledgeWriter(Protocol):
    def upload(
        self, files: tuple[KnowledgeUploadSource, ...]
    ) -> KnowledgeUploadResult: ...

    def recompile(
        self,
        *,
        document: str | None,
        all_documents: bool,
        refresh_schema: bool,
    ) -> KnowledgeRecompileResult: ...


class KnowledgeBackend(KnowledgeReader, KnowledgeWriter, Protocol):
    def close(self) -> None: ...
