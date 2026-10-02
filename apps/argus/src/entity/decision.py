"""Immutable inputs and outcomes for batch decisions."""
from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from .observation import Observation
from .evidence import TaskEvidence


@dataclass(frozen=True)
class RouteDecision:
    action: Literal['silence', 'submit', 'steer', 'wait']
    reason: str
    target_task_id: str | None = None

    def __post_init__(self):
        if self.action not in {'silence', 'submit', 'steer', 'wait'}:
            raise ValueError('Invalid route action')
        if (self.action == 'steer') != bool(self.target_task_id):
            raise ValueError('Only steer requires a target task')


@dataclass(frozen=True)
class InterventionResult:
    verdict: Literal['yes', 'no', 'undetermined']
    reason: str


@dataclass(frozen=True)
class TaskRelationResult:
    verdict: Literal['steer', 'wait', 'undetermined']
    reason: str


@dataclass(frozen=True)
class ObservationBatch:
    id: str
    platform: str
    account_id: str
    subject: str
    messages: tuple[Observation, ...]
    opened_at: datetime
    closed_at: datetime

    def __post_init__(self):
        if not self.messages:
            raise ValueError('Batch cannot be empty')
        if any((m.source.platform, m.source.account_id, m.subject.key) !=
               (self.platform, self.account_id, self.subject) for m in self.messages):
            raise ValueError('Mixed batch scope')

    @classmethod
    def from_row(cls, row, members):
        from .qq_observation import QQObservation
        from ..utils.datetime_utils import utc
        return cls(row.id, row.platform, row.account_id, row.subject,
                   tuple(QQObservation.from_row(m) for m in members), utc(row.opened_at), utc(row.closed_at))

    def to_row(self, *, first_ingest_seq, last_ingest_seq, close_reason, status):
        from .records import DecisionBatchRow
        return DecisionBatchRow(id=self.id, platform=self.platform, account_id=self.account_id,
            subject=self.subject, opened_at=self.opened_at, closed_at=self.closed_at,
            first_ingest_seq=first_ingest_seq, last_ingest_seq=last_ingest_seq,
            close_reason=close_reason, status=status, updated_at=self.closed_at)


@dataclass(frozen=True)
class TaskSnapshot:
    workspace_path: str
    workspace_key: str | None
    session_id: str | None
    active_task_ids: tuple[str, ...]
    lifecycle: str
    checked_at: datetime
    tasks: tuple[TaskEvidence, ...] = ()
    queued_task_count: int = 0
    pending_event_count: int = 0
    pending_plugin_event_count: int = 0
    background_work_count: int = 0
    history_records: tuple[dict, ...] = ()
    history_cursor: int = 0
    history_complete: bool = True
    stable: bool = True


@dataclass(frozen=True)
class BatchWindowResult:
    action: str
    deadline: float | None
    reason: str
