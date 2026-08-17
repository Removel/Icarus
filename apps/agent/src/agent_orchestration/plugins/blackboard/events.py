"""BlackboardPlugin 上下文协议。"""

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Literal

from apps.agent.src.agent_orchestration.events import Event
from apps.agent.src.model_config import LLMRole
from apps.agent.src.model_provider.types import ImagePart, Message


@dataclass(frozen=True, kw_only=True)
class ContextBlock:
    source_plugin_id: str
    context_type: str
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "metadata",
            MappingProxyType(dict(self.metadata)),
        )


@dataclass(frozen=True, kw_only=True)
class ContextContributionEvent(Event):
    status: Literal["completed", "failed"]
    context_blocks: list[ContextBlock] = field(default_factory=list)
    error: str | None = None


@dataclass(frozen=True, kw_only=True)
class BlackboardContextReadyEvent(Event):
    """Blackboard 聚合完成后发布的不可变上下文快照。"""

    model_role: LLMRole
    system_prompt: str
    history_messages: list[Message] = field(default_factory=list)
    prompt: str
    input_prompt: str | None = None
    input_images: list[ImagePart] = field(default_factory=list)
    tools: list[str] | None = None
    context_blocks: list[ContextBlock] = field(default_factory=list)
    context_errors: dict[str, str] = field(default_factory=dict)


AgentContextReadyEvent = BlackboardContextReadyEvent
