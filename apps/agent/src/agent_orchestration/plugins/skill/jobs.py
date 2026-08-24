"""Persistent value object and state machine for Skill write Jobs."""

from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Mapping

from apps.agent.src.agent_orchestration.run_control.types import TaskOperationStatus
from apps.agent.src.agent_orchestration.plugins.skill.models import SkillScope


SkillJobOperation = Literal["produce", "evolve"]
SkillJobStatus = Literal["queued", "running", "succeeded", "failed", "interrupted"]
_TERMINAL_STATUSES = frozenset({"succeeded", "failed", "interrupted"})
_TRANSITIONS = {
    "queued": frozenset({"running", "interrupted"}),
    "running": _TERMINAL_STATUSES,
    "succeeded": frozenset(),
    "failed": frozenset(),
    "interrupted": frozenset(),
}


@dataclass(frozen=True)
class SkillJob:
    job_id: str
    operation: SkillJobOperation
    status: SkillJobStatus
    target_name: str
    scope: SkillScope | None
    task_id: str
    run_id: str
    step: int
    path: str | None = None
    error: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    started_at: datetime | None = None
    finished_at: datetime | None = None
    notification_event_id: str | None = None
    notification_status: TaskOperationStatus | None = None

    @property
    def is_terminal(self) -> bool:
        return self.status in _TERMINAL_STATUSES

    def transition(
        self,
        status: SkillJobStatus,
        *,
        path: str | Path | None = None,
        error: str | None = None,
        now: datetime | None = None,
    ) -> "SkillJob":
        if status not in _TRANSITIONS[self.status]:
            raise ValueError(
                f"Invalid Skill Job transition: {self.status} -> {status}"
            )
        timestamp = now or datetime.now(UTC)
        return replace(
            self,
            status=status,
            path=str(path) if path is not None else self.path,
            error=error,
            started_at=(timestamp if status == "running" else self.started_at),
            finished_at=(timestamp if status in _TERMINAL_STATUSES else None),
        )

    def with_notification_request(self, event_id: str) -> "SkillJob":
        if not self.is_terminal:
            raise ValueError("Only terminal Skill Jobs can request notification")
        if not isinstance(event_id, str) or not event_id.strip():
            raise ValueError("notification event_id cannot be empty")
        return replace(self, notification_event_id=event_id.strip())

    def with_notification_status(
        self, status: TaskOperationStatus
    ) -> "SkillJob":
        if self.notification_event_id is None:
            raise ValueError("Skill Job has no notification request")
        return replace(self, notification_status=status)

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        for field_name in ("created_at", "started_at", "finished_at"):
            field_value = value[field_name]
            value[field_name] = (
                field_value.isoformat() if field_value is not None else None
            )
        return value

    @classmethod
    def create(
        cls,
        *,
        job_id: str,
        operation: SkillJobOperation,
        target_name: str,
        scope: SkillScope | None,
        task_id: str,
        run_id: str,
        step: int,
        now: datetime | None = None,
    ) -> "SkillJob":
        if operation == "produce" and scope not in ("global", "workspace"):
            raise ValueError("Produce Job requires a valid scope")
        if operation == "evolve" and scope is not None:
            raise ValueError("Evolve Job must not define a scope")
        if not all(isinstance(value, str) and value.strip() for value in (job_id, target_name, task_id, run_id)):
            raise ValueError("Skill Job identity fields cannot be empty")
        if not isinstance(step, int) or isinstance(step, bool) or step < 0:
            raise ValueError("Skill Job step must be a non-negative integer")
        return cls(
            job_id=job_id.strip(),
            operation=operation,
            status="queued",
            target_name=target_name.strip(),
            scope=scope,
            task_id=task_id.strip(),
            run_id=run_id.strip(),
            step=step,
            created_at=now or datetime.now(UTC),
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "SkillJob":
        try:
            parsed = dict(value)
            for field_name in ("created_at", "started_at", "finished_at"):
                raw = parsed.get(field_name)
                parsed[field_name] = (
                    datetime.fromisoformat(raw) if isinstance(raw, str) else None
                )
            job = cls(**parsed)
        except (TypeError, ValueError) as error:
            raise ValueError("Invalid persisted Skill Job") from error
        if job.status not in _TRANSITIONS:
            raise ValueError("Invalid persisted Skill Job status")
        return job
