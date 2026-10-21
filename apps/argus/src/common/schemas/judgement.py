"""Rule and judgement results; selected IDs are the write boundary."""

from typing import Literal
from uuid import UUID

from pydantic import Field

from .observation import FrozenSchema, SelectedItem


class Rule(FrozenSchema):
    id: str
    description: str = ''
    priority: int = 0
    semantic: bool = False
    when: dict = Field(default_factory=dict)
    decision: Literal['wake', 'ignore']
    reason: str


class RuleSet(FrozenSchema):
    source: str
    version: int
    digest: str
    mechanical: tuple[Rule, ...]
    semantic: tuple[Rule, ...]
    default_reason: str


class IgnoredSelection(FrozenSchema):
    item_ids: tuple[UUID, ...]
    reason: str


class JudgementOutcome(FrozenSchema):
    selected: tuple[SelectedItem, ...] = ()
    ignored: tuple[IgnoredSelection, ...] = ()
    decision: Literal['wake', 'ignore']
    reason: str
    rule_id: str | None = None
    decision_detail: dict = Field(default_factory=dict)
