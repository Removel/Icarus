from pathlib import Path

from apps.agent.src.agent_orchestration.plugins.knowledge.models import (
    KnowledgeCatalog,
    KnowledgePage,
    KnowledgeQueryResult,
    KnowledgeRecompileDocument,
    KnowledgeRecompileResult,
    KnowledgeUploadItem,
    KnowledgeUploadResult,
)


class KnowledgeBackendStub:
    def __init__(self) -> None:
        self.calls = []
        self.closed = False
        self.catalog = KnowledgeCatalog((), ("summaries/guide",), (), (), ())

    def query(self, question):
        self.calls.append(("query", question))
        return KnowledgeQueryResult("answer:" + question)

    def list(self):
        self.calls.append(("list",))
        return self.catalog

    def read(self, path):
        self.calls.append(("read", path))
        return KnowledgePage(path, "page content")

    def upload(self, sources):
        self.calls.append(("upload", tuple(source.name for source in sources)))
        return KnowledgeUploadResult(
            tuple(
                KnowledgeUploadItem(source.name, "added", "compiled")
                for source in sources
            ),
            len(sources),
            0,
            0,
        )

    def recompile(self, *, document, all_documents, refresh_schema):
        self.calls.append(
            ("recompile", document, all_documents, refresh_schema)
        )
        return KnowledgeRecompileResult(
            "done",
            1,
            1,
            0,
            (
                KnowledgeRecompileDocument(
                    "guide.md", document, "md", "recompiled"
                ),
            ),
        )

    def close(self):
        self.closed = True
