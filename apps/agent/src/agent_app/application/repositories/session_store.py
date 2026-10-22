"""Application data store for Session and public Conversation records."""

from __future__ import annotations

from agent_app.contracts.prompt import SESSION_INSTRUCTIONS_TYPE
from agent_app.application.errors import SessionNotFoundError, SessionAlreadyExistsError, ConversationHistoryCorruptError

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast, get_args
from collections.abc import Mapping

from sqlalchemy import exists, func, select, text, update as update_row
from icarus.runtime.storage import SqliteDatabase
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from icarus.core.identity import (
    SessionIdentity,
)
from agent_app.application.models.status import SessionSummary
from agent_app.application.models.scoped_execution import ExecutionView, ScopedExecutionError, ScopedWakeRequest
from agent_app.argus_protocol import Scope
from icarus.core.session import SessionContext
from agent_app.application.repositories.session_entities import (
    _Base,
    _ConversationUpdateRow,
    _SessionRow,
    _SessionTitleRow,
    _WorkspaceRow,
)
from icarus.core.runtime_update import RuntimeUpdate, RuntimeUpdateType


_SUMMARY_MAX_LENGTH = 256
_UPDATE_TYPES = frozenset(get_args(RuntimeUpdateType))
_INTERNAL_TYPES = frozenset({"argus_report", "argus_report_synced", "argus_report_rejected", "argus_wait_cursor"})


@dataclass(frozen=True)
class InternalRecord:
    sequence: int
    task_id: str | None
    update_type: str
    payload: dict[str, object]


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
    scope_key: str | None = None
    properties: dict[str, object] | None = None

    @property
    def context(self) -> SessionContext:
        properties = self.properties or {}
        return SessionContext(
            kind=str(properties.get("kind", "interactive")),
            scope=cast(Mapping[str, Any] | None, properties.get("scope")),
            metadata=properties,
        )


SoftDeleteStatus = Literal["discarded", "not_empty", "not_found"]


class SessionStore:
    """Persist Session business data behind a domain-oriented API."""

    def __init__(self, data_dir: str | Path) -> None:
        resolved = Path(data_dir).expanduser()
        if not resolved.is_absolute():
            raise ValueError("ICARUS_DATA_DIR must be an absolute path")
        self.data_dir = resolved.resolve()
        self.database_path = self.data_dir / "icarus.db"
        self._database = SqliteDatabase(self.database_path)
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
        await self._database.start()
        engine = self._database.engine
        assert engine is not None
        try:
            async with engine.begin() as connection:
                await connection.run_sync(_Base.metadata.create_all)
                columns = {row[1] for row in (await connection.execute(text("PRAGMA table_info(sessions)")))}
                if "scope_key" not in columns:
                    await connection.execute(text("ALTER TABLE sessions ADD COLUMN scope_key TEXT"))
                if "properties" not in columns:
                    await connection.execute(text("ALTER TABLE sessions ADD COLUMN properties JSON"))
                await connection.execute(text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_sessions_scope ON sessions(scope_key) "
                    "WHERE scope_key IS NOT NULL"
                ))
                await connection.execute(text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_sessions_wake ON sessions(json_extract(properties, '$.wake_id')) "
                    "WHERE json_extract(properties, '$.kind') = 'argus_background'"
                ))
            try:
                self.database_path.chmod(0o600)
            except OSError:
                pass
        except BaseException:
            await self._database.close()
            raise
        self._engine = engine
        self._sessions = self._database.session_factory
        self._started = True

    async def close(self) -> None:
        if not self._started:
            return
        assert self._engine is not None
        await self._database.close()
        self._sessions = None
        self._engine = None
        self._started = False
        self._closed = True

    async def create_session(self, identity: SessionIdentity, *, instructions: str | None = None) -> None:
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
                if instructions is not None:
                    await db.flush()
                    db.add(_ConversationUpdateRow(
                        workspace_key=identity.workspace_key,
                        session_id=identity.session_id,
                        sequence=0,
                        task_id=None,
                        update_type=SESSION_INSTRUCTIONS_TYPE,
                        payload={"content": instructions},
                        occurred_at=now,
                    ))
        except IntegrityError as error:
            raise SessionAlreadyExistsError(identity.session_id) from None
        except SQLAlchemyError:
            # SQL exceptions may contain bound private instruction text.
            raise RuntimeError("Session initialization could not be stored") from None

    async def get_session_instructions(self, identity: SessionIdentity) -> str | None:
        sessions = self._require_started()
        async with sessions() as db:
            session = await db.get(_SessionRow, (identity.workspace_key, identity.session_id))
            if session is None or session.deleted_at is not None:
                raise SessionNotFoundError(identity.session_id)
            row = await db.get(_ConversationUpdateRow, (identity.workspace_key, identity.session_id, 0))
            if row is None:
                return None
            if (row.update_type != SESSION_INSTRUCTIONS_TYPE or row.task_id is not None
                    or not isinstance(row.payload, dict) or set(row.payload) != {"content"}
                    or not isinstance(row.payload["content"], str)):
                raise ConversationHistoryCorruptError("Session instructions contain an invalid record")
            return row.payload["content"]

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
                    func.coalesce(_SessionRow.properties["kind"].as_string(), "interactive") == "interactive",
                )
                .order_by(_SessionRow.session_id.asc())
            )
            return tuple(values)

    async def list_session_summaries(
        self, workspace_key: str
    ) -> tuple[SessionSummary, ...]:
        sessions = self._require_started()
        async with sessions() as db:
            rows = await db.execute(
                select(_SessionRow, _SessionTitleRow.title)
                .outerjoin(
                    _SessionTitleRow,
                    (_SessionRow.workspace_key == _SessionTitleRow.workspace_key)
                    & (_SessionRow.session_id == _SessionTitleRow.session_id),
                )
                .where(
                    _SessionRow.workspace_key == workspace_key,
                    _SessionRow.deleted_at.is_(None),
                    func.coalesce(_SessionRow.properties["kind"].as_string(), "interactive") == "interactive",
                    _SessionRow.first_user_input.is_not(None),
                )
                .order_by(
                    _SessionRow.last_public_activity_at.desc(),
                    _SessionRow.session_id.asc(),
                )
            )
            return tuple(
                SessionSummary(
                    session_id=row.session_id,
                    first_user_input=row.first_user_input or "",
                    title=title,
                    created_at=_as_utc(row.created_at),
                    updated_at=_as_utc(row.updated_at),
                )
                for row, title in rows
            )

    async def get_title(self, identity: SessionIdentity) -> str | None:
        sessions = self._require_started()
        async with sessions() as db:
            row = await db.get(
                _SessionTitleRow, (identity.workspace_key, identity.session_id)
            )
            return row.title if row is not None else None

    async def save_title(self, identity: SessionIdentity, title: str, *, overwrite: bool = False) -> str:
        title = " ".join(title.split()).strip()[:80]
        if not title:
            raise ValueError("Session title cannot be empty")
        sessions = self._require_started()
        async with sessions.begin() as db:
            row = await db.get(
                _SessionRow, (identity.workspace_key, identity.session_id)
            )
            if row is None or row.deleted_at is not None:
                raise SessionNotFoundError(identity.session_id)
            statement = (
                sqlite_insert(_SessionTitleRow)
                .values(
                    workspace_key=identity.workspace_key,
                    session_id=identity.session_id,
                    title=title,
                )
            )
            statement = (
                statement.on_conflict_do_update(
                    index_elements=["workspace_key", "session_id"], set_={"title": title}
                ) if overwrite else statement.on_conflict_do_nothing()
            )
            await db.execute(statement)
            saved = await db.get(
                _SessionTitleRow, (identity.workspace_key, identity.session_id)
            )
            assert saved is not None
            return saved.title

    async def append_update(
        self, identity: SessionIdentity, update: RuntimeUpdate
    ) -> RuntimeUpdate:
        if update.type == SESSION_INSTRUCTIONS_TYPE:
            raise ValueError("Session instructions cannot be appended as public updates")
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

    async def append_internal(self, identity: SessionIdentity, *, task_id: str | None,
                              update_type: str, payload: dict[str, object]) -> int:
        if update_type not in _INTERNAL_TYPES:
            raise ValueError("Unsupported internal update type")
        sessions = self._require_started()
        async with sessions.begin() as db:
            now = datetime.now(UTC)
            sequence = await db.scalar(update_row(_SessionRow).where(
                _SessionRow.workspace_key == identity.workspace_key,
                _SessionRow.session_id == identity.session_id, _SessionRow.deleted_at.is_(None),
            ).values(last_sequence=_SessionRow.last_sequence + 1, updated_at=now).returning(_SessionRow.last_sequence))
            if sequence is None:
                raise SessionNotFoundError(identity.session_id)
            db.add(_ConversationUpdateRow(
                workspace_key=identity.workspace_key, session_id=identity.session_id, sequence=sequence,
                task_id=task_id, update_type=update_type, payload=payload, occurred_at=now,
            ))
            return sequence

    async def read_internal(self, identity: SessionIdentity, *, update_types: set[str],
                            after_sequence: int = 0) -> tuple[InternalRecord, ...]:
        if not update_types <= _INTERNAL_TYPES or after_sequence < 0:
            raise ValueError("Invalid internal update selector")
        sessions = self._require_started()
        async with sessions() as db:
            row = await db.get(_SessionRow, (identity.workspace_key, identity.session_id))
            if row is None:
                raise SessionNotFoundError(identity.session_id)
            rows = await db.scalars(select(_ConversationUpdateRow).where(
                _ConversationUpdateRow.workspace_key == identity.workspace_key,
                _ConversationUpdateRow.session_id == identity.session_id,
                _ConversationUpdateRow.sequence > after_sequence,
                _ConversationUpdateRow.update_type.in_(update_types),
            ).order_by(_ConversationUpdateRow.sequence))
            return tuple(InternalRecord(row.sequence, row.task_id, row.update_type, dict(row.payload)) for row in rows)

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
                records = tuple(_runtime_update(row) for row in rows if row.update_type not in _INTERNAL_TYPES)
                sequences = tuple(row.sequence for row in rows)
                if any(not isinstance(row.payload, dict) for row in rows):
                    raise TypeError("Internal update payload must be an object")
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
                            _ConversationUpdateRow.sequence > 0,
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
                    sequences=sequences,
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
        return await self.soft_delete_session(identity, reason=reason, only_empty=True)

    async def soft_delete_session(
        self,
        identity: SessionIdentity,
        *,
        reason: str,
        only_empty: bool = False,
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
            if only_empty and has_user_message:
                return "not_empty"
            now = datetime.now(UTC)
            row.deleted_at = now
            row.delete_reason = reason
            row.updated_at = now
            return "discarded"

    async def session_kind(self, *, workspace_key: str, session_id: str) -> str | None:
        sessions = self._require_started()
        async with sessions() as db:
            row = await db.get(_SessionRow, (workspace_key, session_id))
            if row is None or row.deleted_at is not None:
                return None
            return str((row.properties or {}).get("kind", "interactive"))

    async def scoped_record(self, wake_id: str) -> SessionRecord | None:
        sessions = self._require_started()
        async with sessions() as db:
            row = await db.scalar(select(_SessionRow).where(
                _SessionRow.properties["wake_id"].as_string() == wake_id,
                _SessionRow.properties["kind"].as_string() == "argus_background",
            ))
            if row is None:
                return None
            workspace = await db.get(_WorkspaceRow, row.workspace_key)
            if workspace is None:
                raise RuntimeError('Stored workspace is unavailable')
            identity = SessionIdentity.create(workspace.workspace_path, row.session_id)
            return _session_record(identity, row)

    async def active_scoped(self) -> tuple[ExecutionView, ...]:
        sessions = self._require_started()
        async with sessions() as db:
            rows = await db.scalars(select(_SessionRow).where(
                _SessionRow.properties["kind"].as_string() == "argus_background", _SessionRow.scope_key.is_not(None),
            ))
            return tuple(_execution_view(row) for row in rows)

    async def reserve_scoped(
        self, request: ScopedWakeRequest, *, identity: SessionIdentity,
        execution_id: str, task_id: str, max_active_executions: int = 8,
    ) -> ExecutionView:
        if not 1 <= max_active_executions <= 100:
            raise ValueError("Invalid execution capacity")
        sessions = self._require_started()
        now = datetime.now(UTC)
        async with sessions() as db:
            async with db.begin():
                await db.execute(text("BEGIN IMMEDIATE"))
                existing = await db.scalar(select(_SessionRow).where(
                    _SessionRow.properties["wake_id"].as_string() == request.wake_id,
                    _SessionRow.properties["kind"].as_string() == "argus_background",
                ))
                if existing is not None:
                    if (existing.properties or {})["fingerprint"] != request.fingerprint:
                        raise ScopedExecutionError("submission_conflict")
                    return _execution_view(existing)
                owner = await db.scalar(select(_SessionRow).where(_SessionRow.scope_key == request.scope.key))
                if owner is not None:
                    raise ScopedExecutionError("scope_busy", owner=_execution_view(owner))
                count = await db.scalar(select(func.count()).select_from(_SessionRow).where(
                    _SessionRow.scope_key.is_not(None),
                    _SessionRow.properties["kind"].as_string() == "argus_background",
                ))
                if (count or 0) >= max_active_executions:
                    raise ScopedExecutionError("capacity_busy")
                await db.execute(sqlite_insert(_WorkspaceRow).values(
                    workspace_key=identity.workspace_key, workspace_path=str(identity.workspace_path),
                    created_at=now, last_seen_at=now,
                ).on_conflict_do_update(index_elements=(_WorkspaceRow.workspace_key,), set_={"last_seen_at": now}))
                view = ExecutionView(
                    wake_id=request.wake_id, origin_boot_id=request.origin_boot_id,
                    execution_id=execution_id, session_id=identity.session_id,
                    task_id=task_id, scope=request.scope,
                )
                properties = {**view.model_dump(mode="json"), "kind": "argus_background", "owner": "argus",
                              "fingerprint": request.fingerprint, "request": request.model_dump(mode="json")}
                db.add(_SessionRow(
                    workspace_key=identity.workspace_key, session_id=identity.session_id,
                    created_at=now, updated_at=now, last_sequence=0,
                    scope_key=request.scope.key, properties=properties,
                ))
                await db.flush()
                return view

    async def get_scoped(self, *, wake_id: str | None = None, scope: Scope | None = None) -> ExecutionView | None:
        if (wake_id is None) == (scope is None):
            raise ValueError("Specify exactly one execution selector")
        sessions = self._require_started()
        async with sessions() as db:
            query = select(_SessionRow).where(_SessionRow.properties["kind"].as_string() == "argus_background")
            query = query.where(_SessionRow.scope_key == scope.key) if scope is not None else query.where(
                _SessionRow.properties["wake_id"].as_string() == wake_id)
            row = await db.scalar(query)
            return _execution_view(row) if row is not None else None

    async def update_scoped(self, wake_id: str, changes: dict[str, object]) -> ExecutionView:
        sessions = self._require_started()
        async with sessions() as db:
            async with db.begin():
                await db.execute(text("BEGIN IMMEDIATE"))
                row = await db.scalar(select(_SessionRow).where(
                    _SessionRow.properties["wake_id"].as_string() == wake_id,
                    _SessionRow.properties["kind"].as_string() == "argus_background",
                ))
                if row is None:
                    raise ScopedExecutionError("not_found")
                immutable = {"wake_id", "origin_boot_id", "execution_id", "session_id", "task_id", "scope", "fingerprint", "kind", "owner", "request", "release_complete"}
                if immutable.intersection(changes):
                    raise ValueError("Execution identity cannot be changed")
                if changes.get('phase') == 'cancelling' and (row.properties or {}).get('execution_stopped'):
                    return _execution_view(row)
                row.properties = {**(row.properties or {}), **changes}
                row.updated_at = datetime.now(UTC)
                return _execution_view(row)

    async def complete_scoped_release(self, wake_id: str) -> ExecutionView:
        sessions = self._require_started()
        async with sessions() as db:
            async with db.begin():
                await db.execute(text("BEGIN IMMEDIATE"))
                row = await db.scalar(select(_SessionRow).where(
                    _SessionRow.properties["wake_id"].as_string() == wake_id,
                    _SessionRow.properties["kind"].as_string() == "argus_background",
                ))
                if row is None:
                    raise ScopedExecutionError("not_found")
                view = _execution_view(row)
                if not view.execution_stopped or view.phase != "stopped" or view.sync_state != "finalized":
                    raise ScopedExecutionError("busy", owner=view)
                row.scope_key = None
                row.properties = {**(row.properties or {}), "release_complete": True}
                row.updated_at = datetime.now(UTC)
                return _execution_view(row)

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


def _execution_view(row: _SessionRow) -> ExecutionView:
    properties = row.properties or {}
    return ExecutionView.model_validate({
        name: properties[name] for name in ExecutionView.model_fields if name in properties
    })


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
        scope_key=row.scope_key,
        properties=row.properties,
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
    sequences: tuple[int, ...] | None = None,
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
    for sequence in sequences if sequences is not None else tuple(record.sequence for record in records):
        if sequence != expected:
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
