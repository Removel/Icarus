"""SnowLuma OneBot v11 history values shared by Icarus applications."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


class SnowLumaError(RuntimeError):
    """A SnowLuma action failed at transport, HTTP or envelope level."""

    def __init__(self, message, *, status=None, retcode=None, wording=None):
        super().__init__(message)
        self.status, self.retcode, self.wording = status, retcode, wording


@dataclass(frozen=True)
class HistoryMessage:
    """One history event normalized from the SnowLuma action response."""

    message_id: str
    sent_at: datetime
    sender_id: str
    sender_name: str | None
    sender_role: str | None
    is_self: bool
    message: tuple[dict[str, Any], ...]
    raw_message: str | None
    group_id: str | None = None
    user_id: str | None = None
    target_id: str | None = None
