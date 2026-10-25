"""Application data store for Session and public Conversation records."""

from __future__ import annotations

from agent_app.contracts.prompt import SESSION_INSTRUCTIONS_TYPE
from agent_app.application.errors import SessionNotFoundError, SessionAlreadyExistsError, ConversationHistoryCorruptError

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast, get_args
from collections.abc import Callable, Mapping

from pydantic import ValidationError
from sqlalchemy.orm import Session

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
from agent_app.application.semantic_context import semantic_subject
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
_INTERNAL_TYPES = frozenset({"argus_execution", "argus_reference", "argus_action", "argus_report", "argus_report_synced", "argus_report_rejected", "argus_wait_cursor"})
_BOUND_TYPES = frozenset({"argus_report", "argus_report_synced", "argus_report_rejected", "argus_wait_cursor", "argus_reference", "argus_action"})


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
        if properties.get("kind") == "argus_background":
            # The context is frozen for the loaded session, not for one wake.
            properties = {key: properties[key] for key in ("kind", "owner", "scope") if key in properties}
        return SessionContext(
            kind=str(properties.get("kind", "interactive")),
            scope=cast(Mapping[str, Any] | None, properties.get("scope")),
            metadata=properties,
        )


SoftDeleteStatus = Literal["discarded", "not_empty", "not_found"]


class SessionStore:
    """Persist Session business data behind a domain-oriented API."""

    def __init__(self, data_dir: str | Path, *, clock: Callable[[], datetime] | None = None) -> None:
        self._clock = clock or (lambda: datetime.now(UTC))
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
                await connection.execute(text("BEGIN IMMEDIATE"))
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
                # Serialize the entire conversion, including outbox attribution. Invalid
                # recovery evidence aborts the transaction rather than inventing an exit.
                await connection.run_sync(_migrate_argus_admissions, self._clock())
                await connection.execute(text("DROP INDEX IF EXISTS uq_sessions_wake"))
                await connection.execute(text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_argus_execution_wake "
                    "ON conversation_updates(json_extract(payload, '$.wake_id')) "
                    "WHERE update_type = 'argus_execution'"
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
        if update_type not in _INTERNAL_TYPES or update_type == "argus_execution":
            raise ValueError("Unsupported internal update type")
        sessions = self._require_started()
        async with sessions.begin() as db:
            now = self._clock()
            session = await db.get(_SessionRow, (identity.workspace_key, identity.session_id))
            if session is None or session.deleted_at is not None:
                raise SessionNotFoundError(identity.session_id)
            if update_type in _BOUND_TYPES and (session.properties or {}).get("kind") == "argus_background":
                admission = await db.scalar(_admission_query().where(
                    _ConversationUpdateRow.workspace_key == identity.workspace_key,
                    _ConversationUpdateRow.session_id == identity.session_id,
                    _ConversationUpdateRow.task_id == task_id,
                ))
                if admission is None or not (admission.payload.get("binding") or {}).get("binding_id"):
                    raise ScopedExecutionError("execution_unknown")
                binding_id = admission.payload["binding"]["binding_id"]
                if (payload.get("wake_id", admission.payload["wake_id"]) != admission.payload["wake_id"]
                        or payload.get("binding_id", binding_id) != binding_id):
                    raise ScopedExecutionError("execution_mismatch")
                payload = {**payload, "wake_id": admission.payload["wake_id"], "binding_id": binding_id}
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
            admission = await db.scalar(_admission_query(wake_id))
            if admission is None:
                return None
            _validate_admission(admission.payload)
            row = await db.get(_SessionRow, (admission.workspace_key, admission.session_id))
            workspace = await db.get(_WorkspaceRow, admission.workspace_key)
            if row is None or workspace is None:
                raise RuntimeError("Stored admission session is unavailable")
            identity = SessionIdentity.create(workspace.workspace_path, row.session_id)
            return replace(_session_record(identity, row), properties={
                **(row.properties or {}), **admission.payload,
            })

    async def load_binding(self, identity: SessionIdentity, task_id: str) -> dict:
        """Select this session's exact task; never fall back to its latest wake."""
        sessions = self._require_started()
        async with sessions() as db:
            row = await db.get(_SessionRow, (identity.workspace_key, identity.session_id))
            admission = await db.scalar(_admission_query().where(
                _ConversationUpdateRow.workspace_key == identity.workspace_key,
                _ConversationUpdateRow.session_id == identity.session_id,
                _ConversationUpdateRow.task_id == task_id,
            ))
            if (row is None or row.deleted_at is not None or admission is None
                    or (row.properties or {}).get("owner") != "argus"):
                raise ScopedExecutionError("execution_unknown")
            payload = admission.payload
            _validate_admission(payload)
            binding = payload.get("binding")
            if not isinstance(binding, dict) or not binding.get("binding_id"):
                raise ScopedExecutionError("execution_unknown")
            return {**binding, "binding": dict(binding), **{name: payload[name] for name in
                ("wake_id", "session_id", "task_id", "execution_id", "scope", "protocol_version")},
                "reason": payload["request"]["reason"], "rule_id": payload["request"]["rule_id"]}

    async def active_scoped(self) -> tuple[ExecutionView, ...]:
        sessions = self._require_started()
        async with sessions() as db:
            rows = await db.scalars(_admission_query().where(
                _ConversationUpdateRow.payload["release_complete"].as_boolean() == False,
            ))
            return tuple(_execution_view(row) for row in rows)

    async def reserve_scoped(
        self, request: ScopedWakeRequest, *, identity: SessionIdentity,
        execution_id: str, task_id: str, max_active_executions: int = 8, retired_at: str | None = None,
    ) -> ExecutionView:
        if not 1 <= max_active_executions <= 100:
            raise ValueError("Invalid execution capacity")
        sessions = self._require_started()
        now = self._clock()
        async with sessions() as db:
            async with db.begin():
                await db.execute(text("BEGIN IMMEDIATE"))
                existing = await db.scalar(_admission_query(request.wake_id))
                if existing is not None:
                    _validate_admission(existing.payload)
                    if existing.payload["fingerprint"] != request.fingerprint:
                        raise ScopedExecutionError("submission_conflict")
                    return _execution_view(existing)
                row = await db.get(_SessionRow, (identity.workspace_key, identity.session_id))
                if row is not None or request.target_session_id is not None:
                    properties = row.properties or {} if row is not None else {}
                    if (row is None or row.deleted_at is not None
                            or properties.get("kind") != "argus_background" or properties.get("owner") != "argus"
                            or properties.get("scope") != request.scope.model_dump(mode="json")
                            or (properties.get('retired_at') is not None and not (
                                request.protocol_version == 2 and request.target_session_id == identity.session_id
                                and retired_at == properties['retired_at']))
                            or (request.target_session_id is not None and request.target_session_id != identity.session_id)):
                        raise ScopedExecutionError("target_unavailable")
                owner = await db.scalar(select(_SessionRow).where(_SessionRow.scope_key == request.scope.key))
                if owner is not None:
                    current_wake_id = (owner.properties or {}).get("current_wake_id")
                    if not isinstance(current_wake_id, str):
                        raise ScopedExecutionError("execution_unknown")
                    current = await db.scalar(_admission_query(current_wake_id))
                    if current is None:
                        raise ScopedExecutionError("execution_unknown")
                    raise ScopedExecutionError("scope_busy", owner=_execution_view(current))
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
                payload = {**view.model_dump(mode="json"), "fingerprint": request.fingerprint,
                    "request": request.model_dump(mode="json"), "protocol_version": request.protocol_version,
                    "state_updated_at": now.isoformat()}
                if row is not None and (row.properties or {}).get('retired_at') is not None:
                    payload['reactivating_retired_at'] = (row.properties or {})['retired_at']
                _validate_admission(payload)
                if row is None:
                    row = _SessionRow(workspace_key=identity.workspace_key, session_id=identity.session_id,
                        created_at=now, updated_at=now, last_sequence=0)
                    db.add(row)
                row.scope_key = request.scope.key
                previous = row.properties or {}
                provisional = payload.get('reactivating_retired_at') is not None
                row.properties = {**previous, "kind": "argus_background", "owner": "argus",
                    "scope": request.scope.model_dump(mode="json"),
                    "summary": previous.get('summary', '') if provisional else request.reason[:256],
                    "last_semantic_activity_at": previous['last_semantic_activity_at'] if provisional else now.isoformat(),
                    "current_wake_id": request.wake_id}
                row.updated_at = now
                row.last_sequence += 1
                await db.flush()
                db.add(_ConversationUpdateRow(workspace_key=identity.workspace_key, session_id=identity.session_id,
                    sequence=row.last_sequence, task_id=task_id, update_type="argus_execution", payload=payload,
                    occurred_at=now))
                await db.flush()
                return view

    async def get_scoped(self, *, wake_id: str | None = None, scope: Scope | None = None) -> ExecutionView | None:
        if (wake_id is None) == (scope is None):
            raise ValueError("Specify exactly one execution selector")
        sessions = self._require_started()
        async with sessions() as db:
            if scope is not None:
                owner = await db.scalar(select(_SessionRow).where(_SessionRow.scope_key == scope.key))
                if owner is None:
                    return None
                current_wake_id = (owner.properties or {}).get("current_wake_id")
                if not isinstance(current_wake_id, str):
                    raise ScopedExecutionError("execution_unknown")
                wake_id = current_wake_id
            row = await db.scalar(_admission_query(wake_id))
            return _execution_view(row) if row is not None else None

    async def update_scoped(self, wake_id: str, changes: dict[str, object]) -> ExecutionView:
        sessions = self._require_started()
        async with sessions() as db:
            async with db.begin():
                await db.execute(text("BEGIN IMMEDIATE"))
                row = await db.scalar(_admission_query(wake_id))
                if row is None:
                    raise ScopedExecutionError("not_found")
                _validate_admission(row.payload)
                immutable = {"wake_id", "origin_boot_id", "execution_id", "session_id", "task_id", "scope", "fingerprint",
                    "kind", "owner", "request", "protocol_version", "target_session_id", "release_complete",
                    'reactivating_retired_at'}
                if immutable.intersection(changes):
                    raise ValueError("Execution identity cannot be changed")
                if changes.get("phase") == "cancelling" and row.payload["execution_stopped"]:
                    return _execution_view(row)
                if "binding" in changes and row.payload.get("binding") is not None and changes["binding"] != row.payload["binding"]:
                    raise ValueError("Execution binding cannot be changed")
                first_stop = changes.get('execution_stopped') is True and not row.payload['execution_stopped']
                payload = {**row.payload, **changes, "state_updated_at": self._clock().isoformat()}
                if payload.get('reactivating_retired_at') is not None:
                    if payload.get('bound') or 'binding' in changes:
                        _validate_reactivation_binding(payload)
                    if payload.get('launching') and not payload.get('bound'):
                        raise ScopedExecutionError('target_unavailable')
                _validate_admission(payload)
                row.payload = payload
                if 'binding' in changes:
                    session = await db.get(_SessionRow, (row.workspace_key, row.session_id))
                    if session is not None and (session.properties or {}).get('current_wake_id') == wake_id:
                        if payload.get('reactivating_retired_at') is not None:
                            if ((session.properties or {}).get('retired_at') != payload['reactivating_retired_at']
                                    or session.scope_key != _execution_view(row).scope.key):
                                raise ScopedExecutionError('target_unavailable')
                            session.properties = {**(session.properties or {}), 'retired_at': None,
                                'last_semantic_activity_at': self._clock().isoformat()}
                        session.properties = {**(session.properties or {}),
                            'summary': semantic_subject(payload['binding'], payload['request'])[:256]}
                if first_stop:
                    # Only the first durable work-stop transition is semantic activity.
                    session = await db.get(_SessionRow, (row.workspace_key, row.session_id))
                    if (session is not None and (session.properties or {}).get('current_wake_id') == wake_id
                            and (session.properties or {}).get('retired_at') is None):
                        session.properties = {**(session.properties or {}),
                            'last_semantic_activity_at': self._clock().isoformat()}
                return _execution_view(row)

    async def complete_scoped_release(self, wake_id: str) -> ExecutionView:
        sessions = self._require_started()
        async with sessions() as db:
            async with db.begin():
                await db.execute(text("BEGIN IMMEDIATE"))
                admission = await db.scalar(_admission_query(wake_id))
                if admission is None:
                    raise ScopedExecutionError("not_found")
                view = _execution_view(admission)
                if not view.execution_stopped or view.phase != "stopped" or view.sync_state != "finalized":
                    raise ScopedExecutionError("busy", owner=view)
                if view.release_complete:
                    return view
                row = await db.get(_SessionRow, (admission.workspace_key, admission.session_id))
                now = self._clock()
                if row is not None and (row.properties or {}).get("current_wake_id") == wake_id:
                    row.scope_key = None
                    row.properties = {**(row.properties or {}), "current_wake_id": None}
                    row.updated_at = now
                admission.payload = {**admission.payload, "release_complete": True, "state_updated_at": now.isoformat()}
                return _execution_view(admission)

    async def argus_lifecycle_sessions(self) -> tuple[SessionRecord, ...]:
        sessions = self._require_started()
        async with sessions() as db:
            rows = await db.execute(select(_SessionRow, _WorkspaceRow.workspace_path).join(
                _WorkspaceRow, _WorkspaceRow.workspace_key == _SessionRow.workspace_key).where(
                _SessionRow.deleted_at.is_(None), _SessionRow.properties['kind'].as_string() == 'argus_background',
                _SessionRow.properties['owner'].as_string() == 'argus',
                _SessionRow.properties['retired_at'].as_string().is_(None)))
            return tuple(_session_record(SessionIdentity.create(path, row.session_id), row) for row, path in rows)

    async def retire_argus_session(self, identity, *, expected_activity: str) -> bool:
        """Retire routing eligibility atomically, never delete conversation history."""
        sessions = self._require_started()
        async with sessions() as db:
            async with db.begin():
                await db.execute(text('BEGIN IMMEDIATE'))
                row = await db.get(_SessionRow, (identity.workspace_key, identity.session_id))
                if (row is None or row.deleted_at is not None or row.scope_key is not None
                        or (row.properties or {}).get('kind') != 'argus_background'
                        or (row.properties or {}).get('last_semantic_activity_at') != expected_activity):
                    return False
                updates = tuple(await db.scalars(select(_ConversationUpdateRow).where(
                    _ConversationUpdateRow.workspace_key == identity.workspace_key,
                    _ConversationUpdateRow.session_id == identity.session_id,
                    _ConversationUpdateRow.update_type.in_({'argus_report', 'argus_report_synced', 'argus_report_rejected'}))))
                terminal = {(value.payload.get('wake_id'), value.payload.get('binding_id'), value.payload.get('report_id'))
                            for value in updates if value.update_type != 'argus_report'}
                if any((value.payload.get('wake_id'), value.payload.get('binding_id'), value.payload.get('report_id'))
                       not in terminal for value in updates if value.update_type == 'argus_report'):
                    return False
                row.properties = {**(row.properties or {}), 'retired_at': self._clock().isoformat()}
                return True

    async def list_argus_sessions(self, scope: Scope, *, cursor=None, limit=10) -> dict:
        if not isinstance(scope, Scope):
            scope = Scope.model_validate(scope)
        if type(limit) is not int or not 1 <= limit <= 20:
            raise ValueError("Invalid candidate page size")
        if cursor is not None and (not isinstance(cursor, str) or len(cursor) > 2048):
            raise ValueError("Invalid candidate cursor")
        import json
        offset = 0
        if cursor is not None:
            try:
                key, offset = json.loads(cursor)
                if key != scope.key or type(offset) is not int or offset < 0:
                    raise ValueError
            except (ValueError, TypeError):
                raise ValueError("Invalid candidate cursor") from None
        sessions = self._require_started()
        async with sessions() as db:
            rows = tuple(await db.scalars(select(_SessionRow).where(
                _SessionRow.deleted_at.is_(None), _SessionRow.properties["kind"].as_string() == "argus_background",
                _SessionRow.properties["owner"].as_string() == "argus",
                _SessionRow.properties["retired_at"].as_string().is_(None),
                _SessionRow.properties['scope']['source'].as_string() == scope.source,
                _SessionRow.properties['scope']['account_id'].as_string() == scope.account_id,
                _SessionRow.properties['scope']['chat_kind'].as_string() == scope.chat_kind,
                _SessionRow.properties['scope']['external_id'].as_string() == scope.external_id,
            ).order_by(_SessionRow.properties['last_semantic_activity_at'].as_string().desc(), _SessionRow.session_id)))
            rows = tuple(row for row in rows if (row.properties or {}).get("scope") == scope.model_dump(mode="json"))
            page = rows[offset:offset+limit]
            items = []
            for row in page:
                pending = tuple(await db.scalars(select(_ConversationUpdateRow).where(
                    _ConversationUpdateRow.workspace_key == row.workspace_key,
                    _ConversationUpdateRow.session_id == row.session_id,
                    _ConversationUpdateRow.update_type.in_({"argus_report", "argus_report_synced", "argus_report_rejected"}),
                )))
                terminal = {(r.payload.get("wake_id"), r.payload.get("binding_id"), r.payload.get("report_id"))
                    for r in pending if r.update_type != "argus_report"}
                outbox = any((r.payload.get("wake_id"), r.payload.get("binding_id"), r.payload.get("report_id")) not in terminal
                    for r in pending if r.update_type == "argus_report")
                properties = row.properties or {}
                items.append({"session_id": row.session_id, "summary": str(properties.get("summary", ""))[:256],
                    "last_related_at": properties["last_semantic_activity_at"],
                    "has_work": row.scope_key is not None or outbox})
            return {"items": items, "next_cursor": json.dumps([scope.key, offset+limit], separators=(",", ":"))
                if offset+limit < len(rows) else None}

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


def _admission_query(wake_id: str | None = None):
    query = select(_ConversationUpdateRow).where(_ConversationUpdateRow.update_type == "argus_execution")
    return query.where(_ConversationUpdateRow.payload["wake_id"].as_string() == wake_id) if wake_id is not None else query


def _validate_reactivation_binding(payload):
    """Authenticated bind proves the frozen target/items before routing revival."""
    binding, request = payload.get('binding'), payload['request']
    if not isinstance(binding, dict):
        raise ScopedExecutionError('target_unavailable')
    expected = {key: payload[key] for key in
                ('protocol_version', 'execution_id', 'session_id', 'task_id', 'scope', 'origin_boot_id')}
    expected['request_fingerprint'] = payload['fingerprint']
    if any(binding.get(key) != value for key, value in expected.items()):
        raise ScopedExecutionError('target_unavailable')
    initial = binding.get('initial_items')
    requested = {item['observation_id'] for item in request['items']}
    if (not isinstance(initial, list) or {item.get('observation_id') for item in initial if isinstance(item, dict)} != requested
            or any(not isinstance(item, dict) for item in initial)
            or len(initial) != len(requested)):
        raise ScopedExecutionError('target_unavailable')


def _validate_admission(payload: dict) -> ExecutionView:
    try:
        allowed = set(ExecutionView.model_fields) | {"fingerprint", "request", "protocol_version", "state_updated_at",
            "deadline_at", "binding", "binding_attempted", "bound", "launching", "deadline_expired",
            "launch_error", "stop_error", "cleanup_error", "reactivating_retired_at"}
        if (not isinstance(payload, dict) or not set(ExecutionView.model_fields) <= payload.keys()
                or payload.keys() - allowed):
            raise ValueError("Incomplete or unsupported execution evidence")
        view = ExecutionView.model_validate({key: payload[key] for key in ExecutionView.model_fields})
        request = ScopedWakeRequest.model_validate(payload["request"])
        if (request.wake_id != view.wake_id or request.origin_boot_id != view.origin_boot_id or request.scope != view.scope
                or request.fingerprint != payload["fingerprint"] or request.protocol_version != payload["protocol_version"]
                or (request.target_session_id is not None and request.target_session_id != view.session_id)):
            raise ValueError("Inconsistent admission identity")
        datetime.fromisoformat(payload["state_updated_at"])
        if 'reactivating_retired_at' in payload:
            retired = datetime.fromisoformat(payload['reactivating_retired_at'])
            if retired.tzinfo is None or request.protocol_version != 2 or request.target_session_id != view.session_id:
                raise ValueError('Invalid provisional target admission')
            if payload.get('bound'):
                _validate_reactivation_binding(payload)
        if "deadline_at" in payload:
            datetime.fromisoformat(payload["deadline_at"])
        for key in ("binding_attempted", "bound", "launching", "deadline_expired"):
            if key in payload and type(payload[key]) is not bool:
                raise ValueError("Invalid admission gate")
        if "binding" in payload:
            binding = payload["binding"]
            if not isinstance(binding, dict) or not isinstance(binding.get("binding_id"), str) or not binding["binding_id"]:
                raise ValueError("Invalid admission binding")
            for key in ("wake_id", "execution_id", "session_id", "task_id", "scope", "protocol_version"):
                if key in binding and binding[key] != payload[key]:
                    raise ValueError("Admission binding identity mismatch")
        for key in ("launch_error", "stop_error", "cleanup_error"):
            if key in payload and not isinstance(payload[key], str):
                raise ValueError("Invalid admission diagnostic")
        return view
    except (KeyError, TypeError, ValueError, ValidationError):
        raise RuntimeError("Stored Argus admission is unavailable") from None


def _migrate_argus_admissions(connection, now):
    with Session(bind=connection) as db:
        rows = tuple(db.scalars(select(_SessionRow).where(
            _SessionRow.properties["kind"].as_string() == "argus_background")))
        for row in rows:
            properties = dict(row.properties or {})
            if "wake_id" not in properties:
                continue
            # Validate all persisted exit and recovery fields before stripping the
            # old projection. No defaults may turn unknown work into released work.
            payload = {key: value for key, value in properties.items() if key not in {"kind", "owner"}}
            try:
                saved_request = ScopedWakeRequest.model_validate(payload.get("request", {}))
            except ValidationError:
                raise RuntimeError("Stored Argus admission is unavailable") from None
            payload["protocol_version"] = saved_request.protocol_version
            payload["state_updated_at"] = _as_utc(row.updated_at).isoformat()
            _validate_admission(payload)
            if properties.get("owner") != "argus":
                raise RuntimeError("Stored Argus admission owner is unavailable")
            previous = db.scalar(_admission_query(saved_request.wake_id))
            if previous is not None:
                raise RuntimeError("Stored Argus admission has competing authorities")
            binding = payload.get("binding")
            binding_id = binding.get("binding_id") if isinstance(binding, dict) else None
            outbox = tuple(db.scalars(select(_ConversationUpdateRow).where(
                _ConversationUpdateRow.workspace_key == row.workspace_key,
                _ConversationUpdateRow.session_id == row.session_id,
                _ConversationUpdateRow.update_type.in_(_BOUND_TYPES))))
            for event in outbox:
                if binding_id is None:
                    raise RuntimeError("Stored Argus admission outbox binding is unavailable")
                event.payload = {**event.payload, "wake_id": properties["wake_id"], "binding_id": binding_id}
            row.last_sequence += 1
            db.add(_ConversationUpdateRow(workspace_key=row.workspace_key, session_id=row.session_id,
                sequence=row.last_sequence, task_id=payload["task_id"], update_type="argus_execution",
                payload=payload, occurred_at=now))
            row.properties = {"kind": "argus_background", "owner": "argus", "scope": payload["scope"],
                "summary": saved_request.reason[:256], "last_semantic_activity_at": _as_utc(row.updated_at).isoformat(),
                "current_wake_id": payload["wake_id"] if row.scope_key is not None else None}
        db.flush()


def _execution_view(row: _ConversationUpdateRow) -> ExecutionView:
    return _validate_admission(row.payload)



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
