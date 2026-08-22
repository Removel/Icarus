"""编排层通用事件。"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import ClassVar
from uuid import uuid4


@dataclass(frozen=True, kw_only=True)
class Event:
    """未来插件系统可以复用的不可变事件基类。"""

    trace_event_flow: ClassVar[bool] = True

    event_id: str = field(default_factory=lambda: uuid4().hex)
    occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    correlation_id: str | None = None
