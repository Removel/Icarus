"""SQLite engine/session lifecycle without application tables or data policy."""

from pathlib import Path

from sqlalchemy import event
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine


def configure_connection(connection, _record) -> None:
    cursor = connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.execute("PRAGMA journal_mode=WAL")
    finally:
        cursor.close()


class SqliteDatabase:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.engine: AsyncEngine | None = None
        self._sessions: async_sessionmaker[AsyncSession] | None = None
        self._closed = False

    @property
    def session_factory(self) -> async_sessionmaker[AsyncSession]:
        if self._sessions is None:
            raise RuntimeError("Database is not running")
        return self._sessions

    async def start(self) -> None:
        if self.engine is not None:
            return
        if self._closed:
            raise RuntimeError("Database cannot be restarted")
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        engine = create_async_engine(URL.create("sqlite+aiosqlite", database=str(self.path)))
        event.listen(engine.sync_engine, "connect", configure_connection)
        self.engine = engine
        self._sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def close(self) -> None:
        if self.engine is not None:
            await self.engine.dispose()
        self.engine = None
        self._sessions = None
        self._closed = True
