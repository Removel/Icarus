import pytest
from sqlalchemy import text


async def test_start_creates_exactly_two_business_tables(database):
    async with database.session() as session:
        names = set((await session.execute(text(
            'SELECT tablename FROM pg_tables WHERE schemaname = current_schema()'
        ))).scalars())
    assert names == {'observed_items', 'wake_requests', 'schema_version'}
    assert await database.is_available() is True


async def test_unknown_version_closes_partial_engine(database):
    from apps.argus.src.core.database import Database, SchemaVersionError
    async with database.begin() as session:
        await session.execute(text('UPDATE schema_version SET version=99'))
    fresh = Database(database.dsn, schema=database.schema)
    with pytest.raises(SchemaVersionError):
        await fresh.start()
    assert await fresh.is_available() is False


async def test_closed_database_is_unavailable(database):
    await database.close()
    assert await database.is_available() is False
