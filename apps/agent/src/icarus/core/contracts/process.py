"""Public session process state and update contracts."""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, TypeAlias
from icarus.core.events import Event

ProcessStatus: TypeAlias = Literal["running", "completed", "failed", "stopped"]


@dataclass(frozen=True)
class ProcessSnapshot:
    process_id: str
    pid: int
    command: str
    workdir: str
    status: ProcessStatus
    started_at: datetime
    ended_at: datetime | None = None
    exit_code: int | None = None
    stop_reason: str | None = None
    log_path: str | None = None
    log_truncated: bool = False
    origin_task_id: str | None = None

    def to_dict(self, *, include_log_path: bool = True) -> dict[str, object]:
        value: dict[str, object] = {
            "process_id": self.process_id,
            "pid": self.pid,
            "command": self.command,
            "workdir": self.workdir,
            "status": self.status,
            "started_at": self.started_at.isoformat().replace("+00:00", "Z"),
            "ended_at": (
                self.ended_at.isoformat().replace("+00:00", "Z")
                if self.ended_at is not None
                else None
            ),
            "exit_code": self.exit_code,
            "stop_reason": self.stop_reason,
            "log_truncated": self.log_truncated,
            "origin_task_id": self.origin_task_id,
        }
        if include_log_path:
            value["log_path"] = self.log_path
        return value



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
