"""QQ-specific observation facts."""

from dataclasses import dataclass
from typing import Literal

from .observation import Observation


@dataclass(frozen=True, kw_only=True)
class QQObservation(Observation):
    message_type: Literal["group", "private"]
    group_id: str | None
    is_self: bool
    mentions_bot: bool
