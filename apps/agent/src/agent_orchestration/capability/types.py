"""Agent 能力层统一类型。"""

from dataclasses import dataclass, field

from apps.agent.src.agent_orchestration.events import Event
from apps.agent.src.agent_orchestration.tools.types import ToolExecutionResult
from apps.agent.src.model_provider.types import (
    FinishReason,
    Message,
    ToolCall,
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


@dataclass(frozen=True, kw_only=True)
class AgentTextDeltaEvent(Event):
    step: int
    text: str


@dataclass(frozen=True, kw_only=True)
class AgentToolStartedEvent(Event):
    step: int
    tool_call: ToolCall


@dataclass(frozen=True, kw_only=True)
class AgentToolCompletedEvent(Event):
    step: int
    tool_call: ToolCall
    result: ToolExecutionResult


@dataclass(frozen=True, kw_only=True)
class AgentCompletedEvent(Event):
    step: int
    response: AgentResponse


@dataclass(frozen=True, kw_only=True)
class AgentErrorEvent(Event):
    step: int
    error_type: str
    error_message: str
