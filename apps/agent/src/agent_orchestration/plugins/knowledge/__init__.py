"""Knowledge Plugin backed by an external knowledge service."""

from apps.agent.src.agent_orchestration.plugins.knowledge.models import (
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
from apps.agent.src.agent_orchestration.plugins.knowledge.openkb_http_adapter import (
    KnowledgeBackendError,
    OpenKBHttpAdapter,
)
from apps.agent.src.agent_orchestration.plugins.knowledge.plugin import (
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
