"""Knowledge Plugin backed by an external knowledge service."""

from agent_app.plugins.knowledge.models import (
    KnowledgeCatalog,
    KnowledgeDocument,
    KnowledgePage,
    KnowledgeQueryResult,
    KnowledgeRecompileDocument,
    KnowledgeRecompileResult,
    KnowledgeUploadItem,
    KnowledgeUploadResult,
    KnowledgeUploadSource,
)
from agent_app.plugins.knowledge.openkb_http_adapter import (
    KnowledgeBackendError,
    OpenKBHttpAdapter,
)
from agent_app.plugins.knowledge.plugin import (
    KnowledgeOperationError,
    KnowledgePlugin,
)

__all__ = [
    "KnowledgeBackendError",
    "KnowledgeCatalog",
    "KnowledgeDocument",
    "KnowledgeOperationError",
    "KnowledgePage",
    "KnowledgePlugin",
    "KnowledgeQueryResult",
    "KnowledgeRecompileDocument",
    "KnowledgeRecompileResult",
    "KnowledgeUploadItem",
    "KnowledgeUploadResult",
    "KnowledgeUploadSource",
    "OpenKBHttpAdapter",
]
