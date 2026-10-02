"""Validated external delivery facts, not Argus batch states."""
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class DeliveryOutcome:
    kind: Literal['accepted','rejected','unknown','failed']
    receipt: dict | None = None
    task_id: str | None = None
    error_type: str | None = None
