"""Flat immutable actions understood by Icarus TUI views."""

from dataclasses import dataclass
from typing import Literal, TypeAlias


@dataclass(frozen=True)
class AppendUserMessage:
    task_id: str
    text: str


@dataclass(frozen=True)
class AppendAssistantDelta:
    task_id: str
    text: str


@dataclass(frozen=True)
class CompleteAssistantMessage:
    task_id: str
    text: str


@dataclass(frozen=True)
class AppendToolStarted:
    task_id: str
    call_id: str
    tool_name: str
    arguments_json: str


@dataclass(frozen=True)
class UpdateToolCompleted:
    task_id: str
    call_id: str
    tool_name: str
    success: bool
    error: str | None = None


@dataclass(frozen=True)
class AppendError:
    task_id: str
    error_type: str
    message: str


@dataclass(frozen=True)
class SetRuntimeStatus:
    task_id: str
    status: Literal["accepted", "running"]
    text: str


@dataclass(frozen=True)
class ShowNotification:
    level: Literal["information", "warning", "error"]
    text: str


@dataclass(frozen=True)
class FinishTurn:
    task_id: str
    status: Literal["completed", "failed", "cancelled", "interrupted"]


UiAction: TypeAlias = (
    AppendUserMessage
    | AppendAssistantDelta
    | CompleteAssistantMessage
    | AppendToolStarted
    | UpdateToolCompleted
    | AppendError
    | SetRuntimeStatus
    | ShowNotification
    | FinishTurn
)
