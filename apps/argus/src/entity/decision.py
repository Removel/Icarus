"""Immutable inputs and outcomes for batch decisions."""
from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from .observation import Observation


@dataclass(frozen=True)
class RouteDecision:
    action: Literal['silence', 'submit', 'steer']
    reason: str
    target_task_id: str | None = None

    def __post_init__(self):
        if self.action not in {'silence', 'submit', 'steer'}:
            raise ValueError('Invalid route action')
        if (self.action == 'steer') != bool(self.target_task_id):
            raise ValueError('Only steer requires a target task')


@dataclass(frozen=True)
class InterventionResult:
    verdict: Literal['yes', 'no', 'undetermined']
    reason: str


@dataclass(frozen=True)
class TaskRelationResult:
    verdict: Literal['supplement', 'new_topic', 'undetermined']
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


@dataclass(frozen=True)
class TaskSnapshot:
    workspace_path: str
    workspace_key: str | None
    session_id: str | None
    active_task_ids: tuple[str, ...]
    lifecycle: str
    checked_at: datetime
