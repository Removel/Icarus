"""Agent 能力层统一类型。"""

from dataclasses import dataclass, field
from collections.abc import Mapping
from copy import deepcopy
from typing import ClassVar

from icarus.core.events import Event
from icarus.core.tools.types import ToolExecutionResult
from icarus.core.models.messages import (
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
class AgentThinkingDeltaEvent(Event):
    trace_event_flow: ClassVar[bool] = False

    step: int
    text: str


@dataclass(frozen=True, kw_only=True)
class AgentThinkingCompletedEvent(Event):
    trace_event_flow: ClassVar[bool] = False

    step: int
    text: str
    partial: bool = False


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

    @property
    def target_tool_call(self) -> ToolCall:
        """Return the dispatch request, not proof of target side effects."""
        from icarus.core.tools.tool_execute import DISPATCHED_TOOL_METADATA

        if self.tool_call.name != "tool_execute" or not isinstance(self.result.metadata, Mapping):
            return self.tool_call
        dispatched = self.result.metadata.get(DISPATCHED_TOOL_METADATA)
        if not isinstance(dispatched, Mapping):
            return self.tool_call
        name, call_id = dispatched.get("name"), dispatched.get("id")
        arguments = dispatched.get("arguments")
        if (not isinstance(name, str) or not name.strip()
                or not isinstance(call_id, str) or not call_id.strip()
                or call_id != self.tool_call.id or not isinstance(arguments, dict)):
            return self.tool_call
        return ToolCall(call_id, name, deepcopy(arguments))


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
