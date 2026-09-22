"""Events published by the Session background process Plugin."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from apps.agent.src.agent_orchestration.events import Event
from apps.agent.src.agent_orchestration.plugins.process.models import (
    ProcessSnapshot,
    ProcessStatus,
)


@dataclass(frozen=True, kw_only=True)
class ProcessUpdatedEvent(Event):
    task_id: str | None = field(default=None, init=False)
    process_id: str
    pid: int
    command: str
    workdir: str
    status: ProcessStatus
    started_at: datetime
    ended_at: datetime | None = None
    exit_code: int | None = None
    stop_reason: str | None = None
    log_truncated: bool = False
    origin_task_id: str | None = None

    @classmethod
    def from_snapshot(cls, snapshot: ProcessSnapshot) -> "ProcessUpdatedEvent":
        return cls(
            process_id=snapshot.process_id,
            pid=snapshot.pid,
            command=snapshot.command,
            workdir=snapshot.workdir,
            status=snapshot.status,
            started_at=snapshot.started_at,
            ended_at=snapshot.ended_at,
            exit_code=snapshot.exit_code,
            stop_reason=snapshot.stop_reason,
            log_truncated=snapshot.log_truncated,
            origin_task_id=snapshot.origin_task_id,
        )
