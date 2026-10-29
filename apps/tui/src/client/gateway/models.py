"""Small TUI-facing values returned by the Gateway client."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SubmitAccepted:
    task_id: str
    queue_position: int


@dataclass(frozen=True)
class TaskOperationResult:
    task_id: str | None
    status: str
    run_id: str | None = None
