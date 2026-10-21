"""Wake request persistence, including frozen submission and explicit outcome."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, Integer, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from .observation import Base


class WakeRow(Base):
    __tablename__ = 'wake_requests'

    id: Mapped[UUID] = mapped_column(PGUUID, primary_key=True)
    boot_id: Mapped[UUID] = mapped_column(PGUUID)
    chat_id: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text)
    rule_id: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict] = mapped_column(JSONB)
    decision_detail: Mapped[dict] = mapped_column(JSONB)
    outcome: Mapped[dict | None] = mapped_column(JSONB)
    attempt_count: Mapped[int] = mapped_column(Integer)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_type: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
