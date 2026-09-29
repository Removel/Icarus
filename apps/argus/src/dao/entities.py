"""Private Argus database mappings."""

from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, JSON, String, UniqueConstraint, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class _Base(DeclarativeBase):
    pass


class _ObservationRow(_Base):
    __tablename__ = "observations"
    __table_args__ = (
        UniqueConstraint("platform", "account_id", "source_message_id", name="uq_observation_source"),
        Index("ix_observation_context", "platform", "account_id", "subject", "occurred_at"),
        Index("ix_observation_status", "processing_status", "received_at"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    platform: Mapped[str] = mapped_column(String(64), nullable=False)
    account_id: Mapped[str] = mapped_column(String(128), nullable=False)
    source_message_id: Mapped[str] = mapped_column(String(128), nullable=False)
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    sender_id: Mapped[str] = mapped_column(String(128), nullable=False)
    sender_name: Mapped[str | None] = mapped_column(String(255))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    content: Mapped[list[dict[str, str]]] = mapped_column(JSON, nullable=False)
    subtype: Mapped[str] = mapped_column(String(64), nullable=False)
    detail_version: Mapped[int] = mapped_column(Integer, nullable=False)
    detail: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    processing_status: Mapped[str] = mapped_column(String(40), nullable=False, default="pending")
    strategy_version: Mapped[str | None] = mapped_column(String(64))
    judgment: Mapped[str | None] = mapped_column(String(32))
    reason: Mapped[str | None] = mapped_column(String(128))
    workspace_path: Mapped[str | None] = mapped_column(String)
    workspace_key: Mapped[str | None] = mapped_column(String(64))
    session_id: Mapped[str | None] = mapped_column(String(255))
    submission_id: Mapped[str | None] = mapped_column(String(128))
    task_id: Mapped[str | None] = mapped_column(String(255))
    error_type: Mapped[str | None] = mapped_column(String(128))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class _SessionLinkRow(_Base):
    __tablename__ = "session_links"
    __table_args__ = (
        Index("ix_link_subject", "platform", "account_id", "subject", "ended_at"),
        Index("uq_link_active", "platform", "account_id", "subject", unique=True,
              sqlite_where=text("ended_at IS NULL")),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    platform: Mapped[str] = mapped_column(String(64), nullable=False)
    account_id: Mapped[str] = mapped_column(String(128), nullable=False)
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    workspace_path: Mapped[str] = mapped_column(String, nullable=False)
    workspace_key: Mapped[str] = mapped_column(String(64), nullable=False)
    session_id: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
