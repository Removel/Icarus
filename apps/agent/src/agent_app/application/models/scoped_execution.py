"""Trusted scoped execution identity and public lifecycle views."""

from typing import Literal

from pydantic import Field, field_validator, model_validator

from agent_app.argus_protocol import Scope, WakeRequest, WireModel, uuid_text

ScopedWakeRequest = WakeRequest


class ExecutionView(WireModel):
    wake_id: str
    origin_boot_id: str
    execution_id: str
    session_id: str
    task_id: str
    scope: Scope
    phase: Literal["preparing", "queued", "running", "cancelling", "stopped"] = "preparing"
    exit_kind: Literal["completed", "failed", "cancelled", "timed_out", "interrupted", "start_failed"] | None = None
    cancel_requested: bool = False
    execution_stopped: bool = False
    report_cursor: int = Field(default=0, ge=0)
    sync_state: Literal["pending", "pending_sync", "finalized"] = "pending"
    release_complete: bool = False

    _ids = field_validator("wake_id", "origin_boot_id", "execution_id", "session_id", "task_id")(uuid_text)

    @model_validator(mode="after")
    def coherent_exit(self):
        if not self.execution_stopped and self.exit_kind is not None:
            raise ValueError("Running execution cannot have an exit kind")
        if self.release_complete and not (
            self.phase == "stopped" and self.execution_stopped and self.sync_state == "finalized"
        ):
            raise ValueError("Execution has not completed release")
        return self


class ScopedExecutionError(RuntimeError):
    def __init__(self, code: str, *, owner: ExecutionView | None = None):
        super().__init__(code)
        self.code = code
        self.owner = owner
        self.retryable = code in {"scope_busy", "capacity_busy", "busy", "execution_unknown", "unavailable"}
