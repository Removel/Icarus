"""Table records, separate from business objects."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, Integer, JSON, String, UniqueConstraint, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class ObservationRow(Base):
    __tablename__ = "observations"
    __table_args__ = (
        UniqueConstraint("platform", "account_id", "source_message_id", name="uq_observation_source"),
        Index("ix_observation_context", "platform", "account_id", "subject", "occurred_at"),
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
    ingest_seq: Mapped[int | None] = mapped_column(Integer, unique=True)
    is_strong_signal: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    batch_id: Mapped[str | None] = mapped_column(String(64), index=True)


class SessionLinkRow(Base):
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


class DecisionBatchRow(Base):
    __tablename__ = "decision_batches"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    platform: Mapped[str] = mapped_column(String(64), nullable=False)
    account_id: Mapped[str] = mapped_column(String(128), nullable=False)
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    closed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    close_reason: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(40), default="ready", index=True)
    __table_args__ = (
        Index('ix_batch_scope_order', 'platform', 'account_id', 'subject', 'first_ingest_seq'),
        Index('ix_batch_delivery', 'platform', 'account_id', 'subject', 'delivered_at'),
        Index('ix_batch_waiting', 'status', 'next_check_at'),
    )
    first_ingest_seq: Mapped[int] = mapped_column(Integer, nullable=False)
    last_ingest_seq: Mapped[int] = mapped_column(Integer, nullable=False)
    judged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    intervention: Mapped[str | None] = mapped_column(String(10))
    judged_through_seq: Mapped[int | None] = mapped_column(Integer)
    decision_message_seq: Mapped[int | None] = mapped_column(Integer)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_check_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    wait_fingerprint: Mapped[str | None] = mapped_column(String(64))
    resume_status: Mapped[str | None] = mapped_column(String(20))
    delivery_attempts: Mapped[list] = mapped_column(JSON, default=list)
    route: Mapped[dict | None] = mapped_column(JSON)
    trace: Mapped[list] = mapped_column(JSON, default=list)
    workspace_path: Mapped[str | None] = mapped_column(String)
    workspace_key: Mapped[str | None] = mapped_column(String(64))
    session_id: Mapped[str | None] = mapped_column(String(255))
    operation_id: Mapped[str | None] = mapped_column(String(255))
    method: Mapped[str | None] = mapped_column(String(64))
    params: Mapped[dict | None] = mapped_column(JSON)
    receipt: Mapped[dict | None] = mapped_column(JSON)
    error_type: Mapped[str | None] = mapped_column(String(128))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
