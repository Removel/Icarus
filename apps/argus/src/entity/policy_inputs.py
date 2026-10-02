"""Explicit inputs for the judgments actually used by the application."""
from dataclasses import dataclass
from .decision import RouteDecision, TaskSnapshot, TaskRelationResult
from .observation import Observation
from .evidence import QQBatchFacts, RecentActivity


@dataclass(frozen=True)
class BatchTimingInput:
    messages: tuple[Observation, ...]
    opened_at: float
    last_human_at: float | None
    now: float
    human_pending_count: int
    has_strong_signal: bool
    activity: RecentActivity


@dataclass(frozen=True)
class InterventionInput:
    facts: QQBatchFacts
    snapshot: TaskSnapshot
    activity: RecentActivity
    rechecking: bool = False


@dataclass(frozen=True)
class RelationInput:
    facts: QQBatchFacts
    snapshot: TaskSnapshot


@dataclass(frozen=True)
class ActionInput:
    relation: TaskRelationResult
    snapshot: TaskSnapshot


@dataclass(frozen=True)
class DeliveryCheckInput:
    decision: RouteDecision
    snapshot: TaskSnapshot
    human_seq: int
    expected_fingerprint: str | None
    current_fingerprint: str
    final_status: dict | None = None
    final_human_seq: int | None = None
    expected_session_signature: tuple | None = None
    final_session_signature: tuple | None = None
