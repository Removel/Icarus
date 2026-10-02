"""Immutable evidence, distinct from decisions and delivery promises."""
from dataclasses import dataclass
from datetime import datetime
from .observation import Observation
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .decision import TaskSnapshot


@dataclass(frozen=True)
class TaskEvidence:
    task_id: str
    lifecycle: str | None
    status_known: bool


@dataclass(frozen=True)
class RecentActivity:
    last_judged_at: datetime | None = None
    consecutive_no_action: int = 0
    last_submit_at: datetime | None = None
    submissions_in_window: int = 0
    last_delivery_at: datetime | None = None
    reset_seq: int = 0


@dataclass(frozen=True)
class ConversationEvidence:
    current: tuple[Observation, ...]
    preceding: tuple[Observation, ...]
    following: tuple[Observation, ...]
    through_seq: int
    human_through_seq: int
    truncated: bool


@dataclass(frozen=True)
class QQBatchFacts:
    human_present: bool
    explicit_address: bool
    state: dict


@dataclass(frozen=True)
class DecisionEvidence:
    conversation: ConversationEvidence
    snapshot: 'TaskSnapshot'
    activity: RecentActivity
    facts: QQBatchFacts
    rechecking: bool
