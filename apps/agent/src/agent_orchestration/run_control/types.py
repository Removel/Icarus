"""Task 运行中介入的公共数据类型。"""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from collections.abc import Sequence
from typing import Literal, Protocol, TypeAlias

from apps.agent.src.model_provider.types import Message


class TaskChannelStatus(str, Enum):
    ACCEPTED = "accepted"
    PREPARING_CONTEXT = "preparing_context"
    RUNNING = "running"
    CANCELLING = "cancelling"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


TaskOperationStatus: TypeAlias = Literal[
    "accepted",
    "not_found",
    "not_running",
    "already_cancelling",
    "already_finished",
    "invalid_content",
]


@dataclass(frozen=True)
class TaskOperationResult:
    task_id: str | None
    status: TaskOperationStatus
    run_id: str | None = None


@dataclass(frozen=True)
class RuntimeContextRecord:
    event_id: str
    task_id: str
    source_id: str
    content: str
    received_at: datetime


@dataclass(frozen=True)
class AppliedContextBatch:
    records: tuple[RuntimeContextRecord, ...]
    message: Message
    applied_before_step: int


class AgentRunControl(Protocol):
    @property
    def task_id(self) -> str:
        ...

    @property
    def run_id(self) -> str | None:
        ...

    @property
    def applied_batches(self) -> tuple[AppliedContextBatch, ...]:
        ...

    @property
    def history_checkpoint(self) -> tuple[Message, ...]:
        ...

    def mark_step(self, step: int) -> None:
        ...

    def raise_if_cancelled(self) -> None:
        ...

    def checkpoint_history(self, messages: Sequence[Message]) -> None:
        ...

    def drain_context(
        self,
        *,
        applied_before_step: int,
    ) -> AppliedContextBatch | None:
        ...

    def close_or_drain(
        self,
        *,
        applied_before_step: int,
    ) -> AppliedContextBatch | None:
        ...
