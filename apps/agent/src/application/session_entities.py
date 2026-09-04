"""Private database mappings for Session business data."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKeyConstraint, Index, Integer, JSON, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class _Base(DeclarativeBase):
    pass


class _WorkspaceRow(_Base):
    __tablename__ = "workspaces"

    workspace_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    workspace_path: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class _SessionRow(_Base):
    __tablename__ = "sessions"
    __table_args__ = (
        ForeignKeyConstraint(
            ("workspace_key",),
            ("workspaces.workspace_key",),
            ondelete="CASCADE",
        ),
        Index(
            "ix_sessions_workspace_deleted_activity",
            "workspace_key",
            "deleted_at",
            "last_public_activity_at",
        ),
    )

    workspace_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    first_user_input: Mapped[str | None] = mapped_column(String(256))
    last_public_activity_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    last_sequence: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delete_reason: Mapped[str | None] = mapped_column(String(64))


class _ConversationUpdateRow(_Base):
    __tablename__ = "conversation_updates"
    __table_args__ = (
        ForeignKeyConstraint(
            ("workspace_key", "session_id"),
            ("sessions.workspace_key", "sessions.session_id"),
            ondelete="CASCADE",
        ),
    )

    workspace_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    sequence: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[str | None] = mapped_column(String(255))
    update_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
