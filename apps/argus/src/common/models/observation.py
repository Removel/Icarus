"""Observed item PostgreSQL mapping; DDL is maintained in db/schema.sql."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import Boolean, DateTime, Integer, Text, ForeignKey
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class ObservationRow(Base):
    __tablename__ = 'observed_items'

    id: Mapped[UUID] = mapped_column(PGUUID, primary_key=True)
    boot_id: Mapped[UUID] = mapped_column(PGUUID)
    source: Mapped[str] = mapped_column(Text)
    chat_id: Mapped[str] = mapped_column(Text)
    message_id: Mapped[str] = mapped_column(Text)
    revision: Mapped[int] = mapped_column(Integer)
    fingerprint: Mapped[str] = mapped_column(Text)
    deleted: Mapped[bool] = mapped_column(Boolean)
    is_self: Mapped[bool] = mapped_column(Boolean)
    strong_signal: Mapped[bool] = mapped_column(Boolean)
    sender_id: Mapped[str] = mapped_column(Text)
    sender_name: Mapped[str | None] = mapped_column(Text)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    content: Mapped[list] = mapped_column(JSONB)
    locator: Mapped[dict] = mapped_column(JSONB)
    detail: Mapped[dict] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(Text)
    status_reason: Mapped[str | None] = mapped_column(Text)
    decision_detail: Mapped[dict | None] = mapped_column(JSONB)
    wake_id: Mapped[UUID | None] = mapped_column(PGUUID, ForeignKey('wake_requests.id'))
