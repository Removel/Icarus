"""Application data store for Session and public Conversation records."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, cast, get_args

from sqlalchemy import event, exists, func, select, update as update_row
from sqlalchemy.engine import URL
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from apps.agent.src.agent_orchestration.plugins.persistence.session_identity import (
    SessionIdentity,
)
from apps.agent.src.application.runtime_status import SessionSummary
from apps.agent.src.application.session_entities import (
    _Base,
    _ConversationUpdateRow,
    _SessionRow,
    _WorkspaceRow,
)
from apps.agent.src.runtime_update import RuntimeUpdate, RuntimeUpdateType


_SUMMARY_MAX_LENGTH = 256
_UPDATE_TYPES = frozenset(get_args(RuntimeUpdateType))


class SessionNotFoundError(KeyError):
    pass


class SessionAlreadyExistsError(ValueError):
    pass


class ConversationHistoryCorruptError(RuntimeError):
    pass


@dataclass(frozen=True)
class SessionRecord:
    identity: SessionIdentity
    created_at: datetime
    updated_at: datetime
    first_user_input: str | None
    last_public_activity_at: datetime | None
    last_sequence: int
    deleted_at: datetime | None
    delete_reason: str | None


SoftDeleteStatus = Literal["discarded", "not_empty", "not_found"]


class SessionStore:
    """Persist Session business data behind a domain-oriented API."""

    def __init__(self, data_dir: str | Path) -> None:
        resolved = Path(data_dir).expanduser()
        if not resolved.is_absolute():
            raise ValueError("ICARUS_DATA_DIR must be an absolute path")
        self.data_dir = resolved.resolve()
        self.database_path = self.data_dir / "icarus.db"
        self._engine: AsyncEngine | None = None
        self._sessions: async_sessionmaker[AsyncSession] | None = None
        self._started = False
        self._closed = False

    async def start(self) -> None:
        if self._started:
            return
        if self._closed:
            raise RuntimeError("SessionStore cannot be restarted")
        database_exists = self.database_path.exists()
        if not database_exists and self._has_legacy_session_data():
            raise RuntimeError(
                "ICARUS_DATA_DIR contains legacy Session data; "
                "use a new empty data directory"
            )
        self.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        engine = create_async_engine(
            URL.create(
                "sqlite+aiosqlite",
                database=str(self.database_path),
            )
        )
        event.listen(engine.sync_engine, "connect", _configure_connection)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(_Base.metadata.create_all)
            try:
                self.database_path.chmod(0o600)
            except OSError:
                pass
        except BaseException:
            await engine.dispose()
            raise
        self._engine = engine
        self._sessions = async_sessionmaker(engine, expire_on_commit=False)
        self._started = True

    async def close(self) -> None:
        if not self._started:
            return
        assert self._engine is not None
        await self._engine.dispose()
        self._sessions = None
        self._engine = None
        self._started = False
        self._closed = True

    async def create_session(self, identity: SessionIdentity) -> None:
        sessions = self._require_started()
        now = datetime.now(UTC)
        try:
            async with sessions.begin() as db:
                await db.execute(
                    sqlite_insert(_WorkspaceRow)
                    .values(
                        workspace_key=identity.workspace_key,
                        workspace_path=str(identity.workspace_path),
                        created_at=now,
                        last_seen_at=now,
                    )
                    .on_conflict_do_update(
                        index_elements=(_WorkspaceRow.workspace_key,),
                        set_={
                            "workspace_path": str(identity.workspace_path),
                            "last_seen_at": now,
                        },
                    )
                )
                row = await db.get(
                    _SessionRow,
                    (identity.workspace_key, identity.session_id),
                )
                if row is not None:
                    raise SessionAlreadyExistsError(identity.session_id)
                db.add(
                    _SessionRow(
                        workspace_key=identity.workspace_key,
                        session_id=identity.session_id,
                        created_at=now,
                        updated_at=now,
                        first_user_input=None,
                        last_public_activity_at=None,
                        last_sequence=0,
                        deleted_at=None,
                        delete_reason=None,
                    )
                )
        except IntegrityError as error:
            raise SessionAlreadyExistsError(identity.session_id) from error

    async def get_session(
        self,
        identity: SessionIdentity,
        *,
        include_deleted: bool = False,
    ) -> SessionRecord | None:
        sessions = self._require_started()
        async with sessions() as db:
            row = await db.get(
                _SessionRow, (identity.workspace_key, identity.session_id)
            )
            if row is None or (row.deleted_at is not None and not include_deleted):
                return None
            return _session_record(identity, row)

    async def session_exists(
        self,
        identity: SessionIdentity,
        *,
        include_deleted: bool = False,
    ) -> bool:
        return (
            await self.get_session(identity, include_deleted=include_deleted)
            is not None
        )

    async def list_session_ids(self, workspace_key: str) -> tuple[str, ...]:
        sessions = self._require_started()
        async with sessions() as db:
            values = await db.scalars(
                select(_SessionRow.session_id)
                .where(
                    _SessionRow.workspace_key == workspace_key,
                    _SessionRow.deleted_at.is_(None),
                )
                .order_by(_SessionRow.session_id.asc())
            )
            return tuple(values)

    async def list_session_summaries(
        self, workspace_key: str
    ) -> tuple[SessionSummary, ...]:
        sessions = self._require_started()
        async with sessions() as db:
            rows = await db.scalars(
                select(_SessionRow)
                .where(
                    _SessionRow.workspace_key == workspace_key,
                    _SessionRow.deleted_at.is_(None),
                    _SessionRow.first_user_input.is_not(None),
                )
                .order_by(
                    _SessionRow.last_public_activity_at.desc(),
                    _SessionRow.session_id.asc(),
                )
            )
            return tuple(
                SessionSummary(row.session_id, row.first_user_input or "")
                for row in rows
            )

    async def append_update(
        self, identity: SessionIdentity, update: RuntimeUpdate
    ) -> RuntimeUpdate:
        if (update.workspace_key, update.session_id) != (
            identity.workspace_key,
            identity.session_id,
        ):
            raise ValueError("Conversation update identity does not match Session")
        if update.sequence is not None:
            raise ValueError("Conversation update already has a sequence")
        sessions = self._require_started()
        async with sessions.begin() as db:
            now = _as_utc(update.occurred_at)
            values: dict[str, object] = {
                "last_sequence": _SessionRow.last_sequence + 1,
                "updated_at": now,
                "last_public_activity_at": now,
            }
            if update.type == "user.message":
                values["first_user_input"] = func.coalesce(
                    _SessionRow.first_user_input,
                    _user_message_summary(update),
                )
            sequence = await db.scalar(
                update_row(_SessionRow)
                .where(
                    _SessionRow.workspace_key == identity.workspace_key,
                    _SessionRow.session_id == identity.session_id,
                    _SessionRow.deleted_at.is_(None),
                )
                .values(**values)
                .returning(_SessionRow.last_sequence)
            )
            if sequence is None:
                raise SessionNotFoundError(identity.session_id)
            recorded = replace(update, sequence=sequence)
            db.add(
                _ConversationUpdateRow(
                    workspace_key=identity.workspace_key,
                    session_id=identity.session_id,
                    sequence=sequence,
                    task_id=recorded.task_id,
                    update_type=recorded.type,
                    payload=dict(recorded.payload),
                    occurred_at=now,
                )
            )
            return recorded

    async def read_updates(
        self,
        identity: SessionIdentity,
        *,
        after_sequence: int = 0,
    ) -> tuple[tuple[RuntimeUpdate, ...], int]:
        if after_sequence < 0:
            raise ValueError("after_sequence cannot be negative")
        sessions = self._require_started()
        async with sessions() as db:
            session_row = await db.get(
                _SessionRow, (identity.workspace_key, identity.session_id)
            )
            if session_row is None or session_row.deleted_at is not None:
                raise SessionNotFoundError(identity.session_id)
            rows = tuple(
                await db.scalars(
                    select(_ConversationUpdateRow)
                    .where(
                        _ConversationUpdateRow.workspace_key
                        == identity.workspace_key,
                        _ConversationUpdateRow.session_id == identity.session_id,
                        _ConversationUpdateRow.sequence > after_sequence,
                    )
                    .order_by(_ConversationUpdateRow.sequence.asc())
                )
            )
            try:
                records = tuple(_runtime_update(row) for row in rows)
                count, minimum, maximum = (
                    await db.execute(
                        select(
                            func.count(_ConversationUpdateRow.sequence),
                            func.min(_ConversationUpdateRow.sequence),
                            func.max(_ConversationUpdateRow.sequence),
                        ).where(
                            _ConversationUpdateRow.workspace_key
                            == identity.workspace_key,
                            _ConversationUpdateRow.session_id
                            == identity.session_id,
                        )
                    )
                ).one()
                _validate_history(
                    records,
                    after_sequence,
                    session_row.last_sequence,
                    count=int(count),
                    minimum=minimum,
                    maximum=maximum,
                )
            except (KeyError, TypeError, ValueError) as error:
                raise ConversationHistoryCorruptError(
                    "Conversation history contains an invalid record"
                ) from error
            return records, session_row.last_sequence

    async def soft_delete_empty_session(
        self,
        identity: SessionIdentity,
        *,
        reason: str,
    ) -> SoftDeleteStatus:
        if not reason.strip():
            raise ValueError("delete reason cannot be empty")
        sessions = self._require_started()
        async with sessions.begin() as db:
            row = await db.get(
                _SessionRow, (identity.workspace_key, identity.session_id)
            )
            if row is None or row.deleted_at is not None:
                return "not_found"
            has_user_message = await db.scalar(
                select(
                    exists().where(
                        _ConversationUpdateRow.workspace_key
                        == identity.workspace_key,
                        _ConversationUpdateRow.session_id == identity.session_id,
                        _ConversationUpdateRow.update_type == "user.message",
                    )
                )
            )
            if has_user_message:
                return "not_empty"
            now = datetime.now(UTC)
            row.deleted_at = now
            row.delete_reason = reason
            row.updated_at = now
            return "discarded"

    def _require_started(self) -> async_sessionmaker[AsyncSession]:
        if not self._started or self._sessions is None:
            raise RuntimeError("SessionStore is not running")
        return self._sessions

    def _has_legacy_session_data(self) -> bool:
        workspaces = self.data_dir / "workspaces"
        if not workspaces.is_dir():
            return False
        return any(
            child.is_dir()
            for workspace in workspaces.iterdir()
            if workspace.is_dir()
            for sessions in (workspace / "sessions",)
            if sessions.is_dir()
            for child in sessions.iterdir()
        )


def _configure_connection(connection, _record) -> None:
    cursor = connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.execute("PRAGMA journal_mode=WAL")
    finally:
        cursor.close()


def _session_record(
    identity: SessionIdentity, row: _SessionRow
) -> SessionRecord:
    return SessionRecord(
        identity=identity,
        created_at=_as_utc(row.created_at),
        updated_at=_as_utc(row.updated_at),
        first_user_input=row.first_user_input,
        last_public_activity_at=(
            _as_utc(row.last_public_activity_at)
            if row.last_public_activity_at is not None
            else None
        ),
        last_sequence=row.last_sequence,
        deleted_at=(
            _as_utc(row.deleted_at) if row.deleted_at is not None else None
        ),
        delete_reason=row.delete_reason,
    )


def _runtime_update(row: _ConversationUpdateRow) -> RuntimeUpdate:
    if row.update_type not in _UPDATE_TYPES:
        raise ValueError("unsupported RuntimeUpdate type")
    if not isinstance(row.payload, dict):
        raise TypeError("RuntimeUpdate payload must be an object")
    return RuntimeUpdate(
        workspace_key=row.workspace_key,
        session_id=row.session_id,
        task_id=row.task_id,
        type=cast(RuntimeUpdateType, row.update_type),
        payload=row.payload,
        occurred_at=_as_utc(row.occurred_at),
        sequence=row.sequence,
    )


def _validate_history(
    records: tuple[RuntimeUpdate, ...],
    after_sequence: int,
    cursor: int,
    *,
    count: int,
    minimum: int | None,
    maximum: int | None,
) -> None:
    if cursor == 0:
        if count != 0 or minimum is not None or maximum is not None:
            raise ValueError("Empty Conversation has persisted records")
    elif count != cursor or minimum != 1 or maximum != cursor:
        raise ValueError("Conversation history sequence is not contiguous")
    if after_sequence >= cursor:
        if records:
            raise ValueError("Conversation history exceeds its cursor")
        return
    expected = after_sequence + 1
    for record in records:
        if record.sequence != expected:
            raise ValueError("Conversation history sequence is not contiguous")
        expected += 1
    if expected - 1 != cursor:
        raise ValueError("Conversation history cursor does not match records")


def _user_message_summary(update: RuntimeUpdate) -> str:
    value = update.payload.get("text")
    text = " ".join(value.split()) if isinstance(value, str) else ""
    if text:
        if len(text) > _SUMMARY_MAX_LENGTH:
            return text[: _SUMMARY_MAX_LENGTH - 1] + "…"
        return text
    resources = update.payload.get("resources")
    if isinstance(resources, list) and resources:
        return "[Image]"
    return "[Message]"


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
