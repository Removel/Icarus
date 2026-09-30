"""Persist Argus observations without exposing ORM rows to services."""

from datetime import UTC, datetime
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from sqlalchemy import event, select, update, text, func
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
        from .schema import check_schema, VERSION
        check_schema(self.database_path)
        engine = create_async_engine(URL.create("sqlite+aiosqlite", database=str(self.database_path)))
        event.listen(engine.sync_engine, "connect", _configure_connection)
        try:
            async with engine.begin() as conn:
                await conn.run_sync(_Base.metadata.create_all)
                await conn.execute(text(f'PRAGMA user_version={VERSION}'))
            self.database_path.chmod(0o600)
        except BaseException:
            await engine.dispose()
            raise
        self._engine = engine
        self._sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def is_available(self) -> bool:
        import asyncio
        from sqlalchemy.exc import SQLAlchemyError
        if self._sessions is None:
            return False
        try:
            async with asyncio.timeout(0.25):
                async with self._sessions() as db:
                    await db.execute(text('SELECT 1 FROM decision_batches LIMIT 1'))
            return True
        except (SQLAlchemyError, TimeoutError):
            return False

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
            await db.execute(text('BEGIN IMMEDIATE'))
            fields['ingest_seq'] = (await db.scalar(select(func.max(_ObservationRow.ingest_seq))) or 0) + 1
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

    async def preceding(self, batch, limit=10):
        async with self._require_sessions()() as db:
            first = await db.scalar(select(func.min(_ObservationRow.ingest_seq)).where(_ObservationRow.batch_id == batch.id))
            if first is None:
                return ()
            rows = (await db.scalars(select(_ObservationRow).where(
                _ObservationRow.platform == batch.platform, _ObservationRow.account_id == batch.account_id,
                _ObservationRow.subject == batch.subject, _ObservationRow.ingest_seq < first,
            ).order_by(_ObservationRow.ingest_seq.desc()).limit(limit))).all()
            return tuple(self._codec.from_row(row) for row in reversed(rows))

    async def unassigned(self, scope: tuple[str, str, str], limit: int = 20):
        async with self._require_sessions()() as db:
            rows = (await db.scalars(select(_ObservationRow).where(
                _ObservationRow.platform == scope[0], _ObservationRow.account_id == scope[1],
                _ObservationRow.subject == scope[2], _ObservationRow.batch_id.is_(None),
            ).order_by(_ObservationRow.ingest_seq).limit(limit))).all()
            return tuple(self._codec.from_row(row) for row in rows)

    async def unassigned_scopes(self):
        async with self._require_sessions()() as db:
            return tuple((await db.execute(select(_ObservationRow.platform, _ObservationRow.account_id,
                _ObservationRow.subject).where(_ObservationRow.batch_id.is_(None)).distinct())).all())

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

    async def status_details(self, id: str) -> dict[str, str | None]:
        sessions = self._require_sessions()
        async with sessions() as db:
            row = await db.get(_ObservationRow, id)
            if row is None:
                raise KeyError(id)
            if row.batch_id is not None:
                from .entities import _DecisionBatchRow
                batch = await db.get(_DecisionBatchRow, row.batch_id)
                return dict(observation_id=id, batch_id=row.batch_id, status=batch.status,
                    session_id=batch.session_id, submission_id=batch.operation_id,
                    task_id=(batch.receipt or {}).get('task_id'), error_type=batch.error_type,
                    route=batch.route)
            return dict(observation_id=id, batch_id=None, status="pending")


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
