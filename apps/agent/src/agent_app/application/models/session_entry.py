"""Shared application session state; one mutation lock per entry."""
from __future__ import annotations
import asyncio
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime
from icarus.core.identity import SessionIdentity
from icarus.core.contracts.input import InputAccepted
from icarus.core.run_control import TaskOperationResult
from agent_app.application.models.status import SessionLifecycle, TaskStatus
from agent_app.application.session_runtime import SessionRuntime

@dataclass(frozen=True)
class _SubmissionRecord:
    fingerprint: str
    accepted: InputAccepted


@dataclass(frozen=True)
class _SteerSubmissionRecord:
    fingerprint: str
    result: TaskOperationResult


@dataclass
class _SessionEntry:
    identity: SessionIdentity
    mutation_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    title_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    runtime: SessionRuntime | None = None
    load_task: asyncio.Task[SessionRuntime] | None = None
    unload_task: asyncio.Task[None] | None = None
    discarding: bool = False
    lifecycle: SessionLifecycle = "unloaded"
    ready_at: datetime | None = None
    last_task_activity_at: datetime | None = None
    error: str | None = None
    persistence_failed: bool = False
    submissions: OrderedDict[str, _SubmissionRecord] = field(
        default_factory=OrderedDict
    )
    steer_submissions: OrderedDict[str, _SteerSubmissionRecord] = field(
        default_factory=OrderedDict
    )
    tasks: OrderedDict[str, TaskStatus] = field(default_factory=OrderedDict)


