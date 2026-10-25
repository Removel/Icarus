"""PostgreSQL engine ownership and explicit transactional sessions."""

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
import re

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

SCHEMA_VERSION = 3


class SchemaVersionError(RuntimeError):
    pass


class Database:
    def __init__(self, dsn: str, *, schema: str = 'public'):
        try:
            url = make_url(dsn)
            if url.drivername != 'postgresql+asyncpg':
                raise ValueError()
        except Exception:
            raise ValueError('A PostgreSQL asyncpg database URL is required') from None
        if not re.fullmatch(r'[a-z_][a-z0-9_]{0,62}', schema):
            raise ValueError('Invalid database schema')
        self.dsn = dsn
        self.schema = schema
        self._engine = None
        self._sessions = None

    async def start(self) -> None:
        if self._engine is not None:
            return
        engine = create_async_engine(self.dsn, pool_pre_ping=True,
                                     connect_args={'server_settings': {'search_path': self.schema}})
        try:
            async with engine.begin() as connection:
                await connection.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
                                         {'key': f'argus-schema:{self.schema}'})
                exists = await connection.scalar(text("SELECT to_regclass('schema_version')"))
                if exists is None:
                    ddl = (Path(__file__).resolve().parents[2] / 'db/schema.sql').read_text()
                    for statement in ddl.split(';'):
                        if statement.strip():
                            await connection.execute(text(statement))
                    await connection.execute(text('INSERT INTO schema_version(version) VALUES (:version)'),
                                             {'version': SCHEMA_VERSION})
                versions = (await connection.execute(text('SELECT version FROM schema_version'))).scalars().all()
                if versions == [1]:
                    migration = Path(__file__).resolve().parents[2] / 'db/migrations/002_agent_handoff.sql'
                    for statement in migration.read_text().split(';'):
                        if statement.strip():
                            await connection.execute(text(statement))
                    await connection.execute(text('UPDATE schema_version SET version=2, applied_at=now()'))
                    versions = [2]
                if versions == [2]:
                    migration = Path(__file__).resolve().parents[2] / 'db/migrations/003_semantic_sessions.sql'
                    for statement in migration.read_text().split(';'):
                        if statement.strip():
                            await connection.execute(text(statement))
                    await connection.execute(text('UPDATE schema_version SET version=3, applied_at=now()'))
                    versions = [3]
                if versions != [SCHEMA_VERSION]:
                    raise SchemaVersionError('Unsupported Argus schema version')
        except BaseException:
            await engine.dispose()
            raise
        self._engine = engine
        self._sessions = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def session(self):
        if self._sessions is None:
            raise RuntimeError('Database is not started')
        async with self._sessions() as session:
            yield session

    @asynccontextmanager
    async def begin(self):
        async with self.session() as session:
            async with session.begin():
                yield session

    async def close(self) -> None:
        engine, self._engine = self._engine, None
        self._sessions = None
        if engine is not None:
            await engine.dispose()

    async def is_available(self) -> bool:
        if self._engine is None:
            return False
        try:
            async with asyncio.timeout(0.25):
                async with self._engine.connect() as connection:
                    return await connection.scalar(text('SELECT 1')) == 1
        except Exception:
            return False
