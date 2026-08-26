"""单个 Task 的运行中信息与取消通道。"""

import asyncio
from collections import deque
from collections.abc import Sequence
from copy import deepcopy
from datetime import UTC, datetime
from threading import RLock
from uuid import uuid4

from apps.agent.src.agent_orchestration.run_control.types import (
    AppliedContextBatch,
    RuntimeContextRecord,
    TaskChannelStatus,
    TaskOperationResult,
)
from apps.agent.src.model_provider.types import Message, TextPart, Usage


class AgentRunCancelled(asyncio.CancelledError):
    """Run Control 在稳定检查点发出的取消信号。"""

    def __init__(self, reason: str | None = None) -> None:
        super().__init__(reason or "task cancellation requested")
        self.reason = reason


class MaxStepsExceededError(RuntimeError):
    """Harness 在准备启动超出上限的模型 Step 时发出。"""

    def __init__(self, max_steps: int, attempted_step: int) -> None:
        super().__init__(
            f"agent run exceeded max_steps={max_steps} "
            f"before step={attempted_step}"
        )
        self.max_steps = max_steps
        self.attempted_step = attempted_step


class TaskChannel:
    """Task 从接受到终态期间唯一的运行控制状态。"""

    def __init__(self, task_id: str, *, max_steps: int = 256) -> None:
        if not task_id.strip():
            raise ValueError("task_id cannot be empty")
        if max_steps < 1:
            raise ValueError("max_steps must be at least 1")
        self.task_id = task_id
        self.max_steps = max_steps
        self._lock = RLock()
        self._status = TaskChannelStatus.ACCEPTED
        self._run_id: str | None = None
        self._context_records: deque[RuntimeContextRecord] = deque()
        self._applied_batches: list[AppliedContextBatch] = []
        self._history_checkpoint: tuple[Message, ...] = ()
        self._history_checkpoint_usage: Usage | None = None
        self._cancel_requested = asyncio.Event()
        self._cancel_reason: str | None = None
        self._current_step = 0
        self._accepting_context = True
        self._completion_claimed = False

    @property
    def status(self) -> TaskChannelStatus:
        with self._lock:
            return self._status

    @property
    def run_id(self) -> str | None:
        with self._lock:
            return self._run_id

    @property
    def current_step(self) -> int:
        with self._lock:
            return self._current_step

    @property
    def cancel_reason(self) -> str | None:
        with self._lock:
            return self._cancel_reason

    @property
    def applied_batches(self) -> tuple[AppliedContextBatch, ...]:
        with self._lock:
            return tuple(self._applied_batches)

    @property
    def history_checkpoint(self) -> tuple[Message, ...]:
        with self._lock:
            return tuple(deepcopy(self._history_checkpoint))

    @property
    def history_checkpoint_usage(self) -> Usage | None:
        with self._lock:
            return self._history_checkpoint_usage

    def mark_preparing_context(self) -> bool:
        with self._lock:
            if self._status != TaskChannelStatus.ACCEPTED:
                return False
            self._status = TaskChannelStatus.PREPARING_CONTEXT
            return True

    def start_run(self, run_id: str) -> bool:
        if not run_id.strip():
            raise ValueError("run_id cannot be empty")
        with self._lock:
            if self._status != TaskChannelStatus.PREPARING_CONTEXT:
                return False
            self._run_id = run_id
            self._status = TaskChannelStatus.RUNNING
            return True

    def add_context(
        self,
        content: str,
        *,
        source_id: str,
        event_id: str | None = None,
        received_at: datetime | None = None,
    ) -> TaskOperationResult:
        if not content.strip() or not source_id.strip():
            return self._result("invalid_content")
        with self._lock:
            if self._status == TaskChannelStatus.CANCELLING:
                return self._result("already_cancelling")
            if not self._accepting_context or self._is_terminal():
                return self._result("already_finished")
            self._context_records.append(
                RuntimeContextRecord(
                    event_id=event_id or uuid4().hex,
                    task_id=self.task_id,
                    source_id=source_id,
                    content=content,
                    received_at=received_at or datetime.now(UTC),
                )
            )
            return self._result("accepted")

    def request_cancel(self, reason: str | None = None) -> TaskOperationResult:
        with self._lock:
            if self._status == TaskChannelStatus.CANCELLING:
                return self._result("already_cancelling")
            if self._completion_claimed or self._is_terminal():
                return self._result("already_finished")
            self._status = TaskChannelStatus.CANCELLING
            self._accepting_context = False
            self._cancel_reason = reason
            self._cancel_requested.set()
            return self._result("accepted")

    def raise_if_cancelled(self) -> None:
        if self._cancel_requested.is_set():
            raise AgentRunCancelled(self.cancel_reason)

    def raise_if_step_exceeded(self, step: int) -> None:
        if step > self.max_steps:
            raise MaxStepsExceededError(self.max_steps, step)

    async def wait_cancel_requested(self) -> None:
        await self._cancel_requested.wait()

    def mark_step(self, step: int) -> None:
        with self._lock:
            self._current_step = step

    def checkpoint_history(
        self,
        messages: Sequence[Message],
        last_usage: Usage | None = None,
    ) -> None:
        with self._lock:
            if self._status != TaskChannelStatus.RUNNING:
                return
            self._history_checkpoint = tuple(deepcopy(list(messages)))
            self._history_checkpoint_usage = last_usage

    def drain_context(self, *, applied_before_step: int) -> AppliedContextBatch | None:
        with self._lock:
            self._raise_if_cancelled_locked()
            return self._drain_context_locked(applied_before_step)

    def close_or_drain(
        self,
        *,
        applied_before_step: int,
    ) -> AppliedContextBatch | None:
        with self._lock:
            self._raise_if_cancelled_locked()
            batch = self._drain_context_locked(applied_before_step)
            if batch is not None:
                return batch
            self._accepting_context = False
            self._completion_claimed = True
            return None

    def mark_failed(self) -> bool:
        with self._lock:
            if self._status == TaskChannelStatus.FAILED:
                return True
            if self._status == TaskChannelStatus.CANCELLING or self._is_terminal():
                return False
            self._accepting_context = False
            self._status = TaskChannelStatus.FAILED
            return True

    def mark_completed(self) -> bool:
        with self._lock:
            if self._status == TaskChannelStatus.COMPLETED:
                return True
            if self._status == TaskChannelStatus.CANCELLING or self._is_terminal():
                return False
            self._accepting_context = False
            self._status = TaskChannelStatus.COMPLETED
            return True

    def mark_cancelled(self) -> bool:
        with self._lock:
            if self._status != TaskChannelStatus.CANCELLING:
                return False
            self._accepting_context = False
            self._status = TaskChannelStatus.CANCELLED
            return True

    def _drain_context_locked(
        self,
        applied_before_step: int,
    ) -> AppliedContextBatch | None:
        if not self._context_records:
            return None
        records = tuple(self._context_records)
        self._context_records.clear()
        content = "\n".join(
            ["<runtime_context>"]
            + [
                f"{index}. {record.content}"
                for index, record in enumerate(records, start=1)
            ]
            + ["</runtime_context>"]
        )
        batch = AppliedContextBatch(
            records=records,
            message=Message("user", [TextPart(content)]),
            applied_before_step=applied_before_step,
        )
        self._applied_batches.append(batch)
        return batch

    def _raise_if_cancelled_locked(self) -> None:
        if self._cancel_requested.is_set():
            raise AgentRunCancelled(self._cancel_reason)

    def _is_terminal(self) -> bool:
        return self._status in {
            TaskChannelStatus.COMPLETED,
            TaskChannelStatus.FAILED,
            TaskChannelStatus.CANCELLED,
        }

    def _result(self, status) -> TaskOperationResult:
        return TaskOperationResult(
            task_id=self.task_id,
            status=status,
            run_id=self._run_id,
        )
