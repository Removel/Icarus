"""Agent 能力层统一类型。"""

from dataclasses import dataclass, field
from typing import ClassVar

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
    last_usage: Usage | None = None
    finish_reason: FinishReason | None = None
    steps: int = 0
    messages: list[Message] = field(default_factory=list)
    task_message_start: int | None = None

    @property
    def task_messages(self) -> tuple[Message, ...]:
        if self.task_message_start is None:
            return ()
        return tuple(self.messages[self.task_message_start :])


@dataclass(frozen=True, kw_only=True)
class AgentTextDeltaEvent(Event):
    trace_event_flow: ClassVar[bool] = False

    step: int
    text: str


@dataclass(frozen=True, kw_only=True)
class AgentMessageCompletedEvent(Event):
    """One complete model response, emitted after its stream is assembled."""

    step: int
    message: Message


@dataclass(frozen=True, kw_only=True)
class AgentToolStartedEvent(Event):
    trace_event_flow: ClassVar[bool] = False

    step: int
    tool_call: ToolCall


@dataclass(frozen=True, kw_only=True)
class AgentToolCompletedEvent(Event):
    trace_event_flow: ClassVar[bool] = False

    step: int
    tool_call: ToolCall
    result: ToolExecutionResult


@dataclass(frozen=True, kw_only=True)
class AgentCompletedEvent(Event):
    step: int
    response: AgentResponse


@dataclass(frozen=True, kw_only=True)
class AgentCancelledEvent(Event):
    step: int
    reason: str | None = None
    task_messages: tuple[Message, ...] = ()
    last_usage: Usage | None = None
