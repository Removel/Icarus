import asyncio
import sqlite3
import pytest
from apps.argus.src.dao.schema import SchemaUpgradeRequired, VERSION
from apps.argus.test.persistence_fakes import ObservationStore


def test_old_trial_database_is_refused_without_modification(tmp_path):
    path = tmp_path / 'argus.db'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE observations (id TEXT)')
        db.execute('PRAGMA user_version=2')
    before = path.read_bytes()
    async def exercise():
        with pytest.raises(SchemaUpgradeRequired, match='explicit rebuilding'):
            await ObservationStore(tmp_path).start()
    asyncio.run(exercise())
    assert path.read_bytes() == before


def test_new_schema_restarts_without_losing_data(tmp_path):
    async def exercise():
        store = ObservationStore(tmp_path)
        await store.start()
        await store.close()
        await store.start()
        assert await store.database.is_available()
        await store.close()
    asyncio.run(exercise())
    with sqlite3.connect(tmp_path / 'argus.db') as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == VERSION == 3
