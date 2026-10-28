"""Read-only generic session work snapshot."""
from dataclasses import dataclass
from datetime import datetime

@dataclass(frozen=True)
class SessionRuntimeSnapshot:
    active_task_ids: tuple[str, ...]
    queued_task_count: int
    pending_event_count: int
    pending_plugin_event_count: int
    background_work_count: int
    last_event_at: datetime | None
    last_background_work_at: datetime | None

    @property
    def has_work(self) -> bool:
        return bool(
            self.active_task_ids
            or self.queued_task_count
            or self.pending_event_count
            or self.pending_plugin_event_count
            or self.background_work_count
        )

