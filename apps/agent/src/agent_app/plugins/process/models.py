"""Internal and public process state models."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import BinaryIO, Literal, TypeAlias


from icarus.core.contracts.process import ProcessStatus, ProcessSnapshot


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
