"""Internal and public process state models."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import BinaryIO, Literal, TypeAlias


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


@dataclass
class ProcessRecord:
    process_id: str
    process: asyncio.subprocess.Process
    process_group_id: int
    command: str
    workdir: Path
    started_at: datetime
    log_path: Path
    log_file: BinaryIO
    log_generation: str
    origin_task_id: str | None = None
    status: ProcessStatus = "running"
    ended_at: datetime | None = None
    exit_code: int | None = None
    stop_requested: bool = False
    stop_reason: str | None = None
    log_truncated: bool = False
    log_bytes: int = 0
    committed: asyncio.Event = field(default_factory=asyncio.Event)
    wake: asyncio.Event = field(default_factory=asyncio.Event)
    supervisor_task: asyncio.Task[None] | None = None
    termination_generation: int = 0
    handled_termination_generation: int = 0
    termination_waiters: dict[int, asyncio.Future[ProcessSnapshot]] = field(
        default_factory=dict
    )
    supervisor_error: str | None = None
    discarded: bool = False

    def snapshot(self) -> ProcessSnapshot:
        return ProcessSnapshot(
            process_id=self.process_id,
            pid=self.process.pid,
            command=self.command,
            workdir=str(self.workdir),
            status=self.status,
            started_at=self.started_at,
            ended_at=self.ended_at,
            exit_code=self.exit_code,
            stop_reason=self.stop_reason,
            log_path=str(self.log_path),
            log_truncated=self.log_truncated,
            origin_task_id=self.origin_task_id,
        )
