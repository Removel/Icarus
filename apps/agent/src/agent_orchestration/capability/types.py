"""Agent 能力层统一类型。"""

from dataclasses import dataclass, field

from apps.agent.src.model_provider.types import (
    FinishReason,
    Message,
    Usage,
)


@dataclass(frozen=True)
class AgentResponse:
    """一次完整 ReAct 调用的最终结果。"""

    message: Message
    reasoning: str | None = None
    usage: Usage | None = None
    finish_reason: FinishReason | None = None
    steps: int = 0
    messages: list[Message] = field(default_factory=list)
