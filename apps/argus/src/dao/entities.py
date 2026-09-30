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
    batch_id: Mapped[str | None] = mapped_column(String(64), index=True)


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


class _DecisionBatchRow(_Base):
    __tablename__ = "decision_batches"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    platform: Mapped[str] = mapped_column(String(64), nullable=False)
    account_id: Mapped[str] = mapped_column(String(128), nullable=False)
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    closed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    close_reason: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(40), default="ready", index=True)
    graph_version: Mapped[str | None] = mapped_column(String(64))
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
