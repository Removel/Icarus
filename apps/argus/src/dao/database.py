"""Own the SQLite connection lifecycle and explicit transaction boundaries."""
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from sqlalchemy import event, text
from sqlalchemy.engine import URL
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from ..entity.records import Base
from .schema import check_schema, VERSION


class Database:
    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir).expanduser().resolve()
        self.database_path = self.data_dir / 'argus.db'
        self._engine = None
        self._sessions = None

    @property
    def is_running(self):
        return self._sessions is not None

    async def start(self):
        if self.is_running:
            return
        self.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        check_schema(self.database_path)
        engine = create_async_engine(URL.create('sqlite+aiosqlite', database=str(self.database_path)))
        event.listen(engine.sync_engine, 'connect', _configure_connection)
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
                await conn.execute(text(f'PRAGMA user_version={VERSION}'))
            self.database_path.chmod(0o600)
        except BaseException:
            await engine.dispose()
            raise
        self._engine = engine
        self._sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def close(self):
        if self._engine is not None:
            await self._engine.dispose()
        self._engine = self._sessions = None

    @asynccontextmanager
    async def session(self):
        if self._sessions is None:
            raise RuntimeError('Database is not running')
        async with self._sessions() as session:
            yield session

    @asynccontextmanager
    async def begin(self, *, immediate=False):
        async with self.session() as session:
            async with session.begin():
                await session.execute(text('BEGIN IMMEDIATE' if immediate else 'BEGIN'))
                yield session

    @asynccontextmanager
    async def use(self, session=None, *, write=False):
        if session is not None:
            yield session
        elif write:
            async with self.begin(immediate=True) as owned:
                yield owned
        else:
            async with self.session() as owned:
                yield owned

    async def is_available(self):
        if not self.is_running:
            return False
        try:
            async with asyncio.timeout(0.25):
                async with self.session() as session:
                    await session.execute(text('SELECT 1 FROM decision_batches LIMIT 1'))
            return True
        except (SQLAlchemyError, TimeoutError):
            return False


def _configure_connection(connection, _record):
    cursor = connection.cursor()
    try:
        cursor.execute('PRAGMA foreign_keys=ON')
        cursor.execute('PRAGMA busy_timeout=5000')
        cursor.execute('PRAGMA journal_mode=WAL')
    finally:
        cursor.close()
