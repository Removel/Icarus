"""Public history page, separate from model-message state."""

from dataclasses import dataclass
from icarus.core.runtime_update import RuntimeUpdate


@dataclass(frozen=True)
class HistoryPage:
    records: tuple[RuntimeUpdate, ...]
    history_cursor: int
    next_after_sequence: int
    has_more: bool
