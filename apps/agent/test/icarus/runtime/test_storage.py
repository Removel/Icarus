"""SQLite connection infrastructure does not own application schema or records."""

import asyncio
import importlib
from sqlalchemy import text


def test_database_manages_wal_and_transactions_without_application_schema(tmp_path):
    async def run():
        api = importlib.import_module("icarus.runtime.storage")
        database = api.SqliteDatabase(tmp_path / "test.db")
        await database.start()
        async with database.session_factory.begin() as session:
            assert await session.scalar(text("PRAGMA journal_mode")) == "wal"
            await session.execute(text("CREATE TABLE example(value INTEGER NOT NULL)"))
            await session.execute(text("INSERT INTO example(value) VALUES (7)"))
        await database.close()
        reopened = api.SqliteDatabase(tmp_path / "test.db")
        await reopened.start()
        async with reopened.session_factory() as session:
            assert await session.scalar(text("SELECT value FROM example")) == 7
        await reopened.close()
    asyncio.run(run())
