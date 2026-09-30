"""Persist Argus observations without exposing ORM rows to services."""

from datetime import UTC, datetime
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from sqlalchemy import event, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from apps.argus.src.entity.qq_observation import QQObservation
from apps.argus.src.entity.observation import Observation
from .entities import _Base, _ObservationRow, _SessionLinkRow
from .qq_observation_codec import QQObservationCodec


class ObservationConflictError(ValueError):
    pass


@dataclass(frozen=True)
class SessionLink:
    platform: str
    account_id: str
    subject: str
    workspace_path: str
    workspace_key: str
    session_id: str


class ObservationStore:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = Path(data_dir).expanduser().resolve()
        self.database_path = self.data_dir / "argus.db"
        self._engine: AsyncEngine | None = None
        self._sessions: async_sessionmaker[AsyncSession] | None = None
        self._codec = QQObservationCodec()

    async def start(self) -> None:
        if self._engine is not None:
            return
        self.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        engine = create_async_engine(URL.create("sqlite+aiosqlite", database=str(self.database_path)))
        event.listen(engine.sync_engine, "connect", _configure_connection)
        try:
            async with engine.begin() as conn:
                await conn.run_sync(_Base.metadata.create_all)
            self.database_path.chmod(0o600)
        except BaseException:
            await engine.dispose()
            raise
        self._engine = engine
        self._sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def close(self) -> None:
        if self._engine is not None:
            await self._engine.dispose()
            self._engine = None
            self._sessions = None

    def _require_sessions(self) -> async_sessionmaker[AsyncSession]:
        if self._sessions is None:
            raise RuntimeError("ObservationStore is not running")
        return self._sessions

    async def insert(self, obs: Observation) -> bool:
        if not isinstance(obs, QQObservation):
            raise ValueError("Unsupported observation type")
        sessions = self._require_sessions()
        fields = self._codec.to_row_fields(obs)
        async with sessions.begin() as db:
            statement = sqlite_insert(_ObservationRow).values(**fields).on_conflict_do_nothing(
                index_elements=("platform", "account_id", "source_message_id")
            )
            result = await db.execute(statement)
            if result.rowcount:
                return True
            row = await db.scalar(select(_ObservationRow).where(
                _ObservationRow.platform == obs.source.platform,
                _ObservationRow.account_id == obs.source.account_id,
                _ObservationRow.source_message_id == obs.source.source_message_id,
            ))
            assert row is not None
            existing = self._codec.from_row(row)
            if not _same_facts(existing, obs):
                raise ObservationConflictError("Conflicting observation for the same source message")
            return False

    async def mark_failed_before_submit(self, id: str, error_type: str) -> None:
        sessions = self._require_sessions()
        async with sessions.begin() as db:
            await db.execute(update(_ObservationRow).where(
                _ObservationRow.id == id,
                _ObservationRow.processing_status == "processing",
                _ObservationRow.submission_id.is_(None),
            ).values(processing_status="failed", error_type=error_type,
                     updated_at=datetime.now(UTC)))

    async def release_before_submit(self, id: str) -> None:
        sessions = self._require_sessions()
        async with sessions.begin() as db:
            await db.execute(update(_ObservationRow).where(
                _ObservationRow.id == id,
                _ObservationRow.processing_status == "processing",
                _ObservationRow.submission_id.is_(None),
            ).values(processing_status="pending", updated_at=datetime.now(UTC)))

    async def claim(self, id: str) -> bool:
        sessions = self._require_sessions()
        async with sessions.begin() as db:
            result = await db.execute(
                update(_ObservationRow).where(
                    _ObservationRow.id == id,
                    _ObservationRow.processing_status == "pending",
                ).values(processing_status="processing", updated_at=datetime.now(UTC))
            )
            return bool(result.rowcount)

    async def mark_silence(self, id: str, reason: str, strategy_version: str) -> None:
        sessions = self._require_sessions()
        async with sessions.begin() as db:
            await db.execute(update(_ObservationRow).where(_ObservationRow.id == id).values(
                processing_status="finished", judgment="silence", reason=reason,
                strategy_version=strategy_version, updated_at=datetime.now(UTC),
            ))

    async def recover(self, *, limit: int = 100) -> tuple[Observation, ...]:
        sessions = self._require_sessions()
        async with sessions() as db:
            rows = (await db.scalars(select(_ObservationRow).where(
                _ObservationRow.processing_status == "pending"
            ).order_by(_ObservationRow.received_at, _ObservationRow.id).limit(limit))).all()
            return tuple(self._codec.from_row(row) for row in rows)

    async def reset_interrupted(self) -> None:
        sessions = self._require_sessions()
        async with sessions.begin() as db:
            await db.execute(update(_ObservationRow).where(
                _ObservationRow.processing_status == "processing",
                _ObservationRow.submission_id.is_(None),
            ).values(processing_status="pending", updated_at=datetime.now(UTC)))
            await db.execute(update(_ObservationRow).where(
                _ObservationRow.processing_status.in_(("processing", "submitting")),
                _ObservationRow.submission_id.is_not(None),
                _ObservationRow.task_id.is_(None),
            ).values(processing_status="submission_unknown", updated_at=datetime.now(UTC)))

    async def reserve_session_creation(self, id: str, workspace_path: str,
                                       session_id: str) -> None:
        sessions = self._require_sessions()
        async with sessions.begin() as db:
            result = await db.execute(update(_ObservationRow).where(
                _ObservationRow.id == id,
                _ObservationRow.processing_status == "processing",
            ).values(workspace_path=workspace_path, session_id=session_id,
                     updated_at=datetime.now(UTC)))
            if not result.rowcount:
                raise ValueError("Observation cannot reserve a session")

    async def pending_session(self, id: str) -> tuple[str, str] | None:
        sessions = self._require_sessions()
        async with sessions() as db:
            row = await db.get(_ObservationRow, id)
            if row is None:
                raise KeyError(id)
            if row.workspace_path is None or row.session_id is None:
                return None
            return row.workspace_path, row.session_id

    async def current_link(self, platform: str, account_id: str, subject: str) -> SessionLink | None:
        sessions = self._require_sessions()
        async with sessions() as db:
            row = await db.scalar(select(_SessionLinkRow).where(
                _SessionLinkRow.platform == platform,
                _SessionLinkRow.account_id == account_id,
                _SessionLinkRow.subject == subject,
                _SessionLinkRow.ended_at.is_(None),
            ))
            if row is None:
                return None
            return SessionLink(row.platform, row.account_id, row.subject,
                               row.workspace_path, row.workspace_key, row.session_id)

    async def save_link(self, link: SessionLink) -> None:
        sessions = self._require_sessions()
        async with sessions.begin() as db:
            await db.execute(update(_SessionLinkRow).where(
                _SessionLinkRow.platform == link.platform,
                _SessionLinkRow.account_id == link.account_id,
                _SessionLinkRow.subject == link.subject,
                _SessionLinkRow.ended_at.is_(None),
            ).values(ended_at=datetime.now(UTC)))
            db.add(_SessionLinkRow(
                id=uuid4().hex, platform=link.platform, account_id=link.account_id,
                subject=link.subject, workspace_path=link.workspace_path,
                workspace_key=link.workspace_key, session_id=link.session_id,
                created_at=datetime.now(UTC),
            ))

    async def reserve_submission(self, id: str, link: SessionLink, submission_id: str,
                                 strategy_version: str) -> bool:
        sessions = self._require_sessions()
        async with sessions.begin() as db:
            result = await db.execute(update(_ObservationRow).where(
                _ObservationRow.id == id,
                _ObservationRow.processing_status == "processing",
                _ObservationRow.submission_id.is_(None),
            ).values(
                processing_status="submitting", judgment="wake",
                strategy_version=strategy_version,
                workspace_path=link.workspace_path, workspace_key=link.workspace_key,
                session_id=link.session_id, submission_id=submission_id,
                updated_at=datetime.now(UTC),
            ))
            return bool(result.rowcount)

    async def mark_submitted(self, id: str, task_id: str) -> None:
        sessions = self._require_sessions()
        async with sessions.begin() as db:
            await db.execute(update(_ObservationRow).where(
                _ObservationRow.id == id, _ObservationRow.processing_status == "submitting"
            ).values(processing_status="submitted", task_id=task_id, updated_at=datetime.now(UTC)))

    async def mark_submission_unknown(self, id: str, error_type: str) -> None:
        sessions = self._require_sessions()
        async with sessions.begin() as db:
            await db.execute(update(_ObservationRow).where(
                _ObservationRow.id == id, _ObservationRow.processing_status == "submitting"
            ).values(processing_status="submission_unknown", error_type=error_type,
                     updated_at=datetime.now(UTC)))

    async def mark_submission_failed(self, id: str, error_type: str) -> None:
        sessions = self._require_sessions()
        async with sessions.begin() as db:
            await db.execute(update(_ObservationRow).where(
                _ObservationRow.id == id, _ObservationRow.processing_status == "submitting"
            ).values(processing_status="failed", error_type=error_type,
                     updated_at=datetime.now(UTC)))

    async def status_details(self, id: str) -> dict[str, str | None]:
        sessions = self._require_sessions()
        async with sessions() as db:
            row = await db.get(_ObservationRow, id)
            if row is None:
                raise KeyError(id)
            return {
                "observation_id": row.id,
                "status": row.processing_status,
                "session_id": row.session_id,
                "submission_id": row.submission_id,
                "task_id": row.task_id,
                "error_type": row.error_type,
                "judgment": row.judgment,
                "reason": row.reason,
            }

    async def status(self, id: str) -> str:
        sessions = self._require_sessions()
        async with sessions() as db:
            row = await db.get(_ObservationRow, id)
            if row is None:
                raise KeyError(id)
            return row.processing_status

    async def get(self, id: str) -> Observation:
        sessions = self._require_sessions()
        async with sessions() as db:
            row = await db.get(_ObservationRow, id)
            if row is None:
                raise KeyError(id)
            return self._codec.from_row(row)

    async def recent(
        self, platform: str, account_id: str, subject: str, limit: int = 20
    ) -> tuple[Observation, ...]:
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        sessions = self._require_sessions()
        async with sessions() as db:
            rows = (await db.scalars(select(_ObservationRow).where(
                _ObservationRow.platform == platform,
                _ObservationRow.account_id == account_id,
                _ObservationRow.subject == subject,
            ).order_by(_ObservationRow.received_at.desc(), _ObservationRow.id.desc()).limit(limit))).all()
            return tuple(self._codec.from_row(row) for row in rows)


def _same_facts(left: Observation, right: Observation) -> bool:
    return (
        left.source == right.source
        and left.subject == right.subject
        and left.actor == right.actor
        and left.content == right.content
        and isinstance(left, QQObservation)
        and isinstance(right, QQObservation)
        and (left.message_type, left.group_id, left.is_self, left.mentions_bot)
        == (right.message_type, right.group_id, right.is_self, right.mentions_bot)
    )


def _configure_connection(connection, _record) -> None:
    cursor = connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.execute("PRAGMA journal_mode=WAL")
    finally:
        cursor.close()
