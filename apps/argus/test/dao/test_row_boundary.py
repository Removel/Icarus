import asyncio
import pytest
from apps.argus.src.dao.database import Database
from apps.argus.src.dao.observation_store import ObservationStore, ObservationConflictError
from apps.argus.src.dao.batch_store import BatchStore
from apps.argus.src.entity.records import ObservationRow
from apps.argus.src.entity.qq_observation import QQObservation
from apps.argus.src.handler.qq_handler import QQHandler
from apps.argus.src.service.batch_state import close_batch


def message(text='hello'):
    return QQHandler('10000').parse(dict(post_type='message',self_id=10000,time=1,
        message_id=1,message_type='group',group_id=42,user_id=7,message=text))


def test_row_dedup_keeps_first_metadata_and_conflicts(tmp_path):
    async def exercise():
        database = Database(tmp_path)
        await database.start()
        store = ObservationStore(database)
        try:
            obs = message()
            assert await store.insert(obs.to_row(is_strong_signal=True))
            assert not await store.insert(message().to_row(is_strong_signal=False))
            row = await store.get(obs.id)
            assert isinstance(row, ObservationRow)
            assert row.ingest_seq == 1 and row.is_strong_signal
            assert QQObservation.from_row(row) == obs
            with pytest.raises(ObservationConflictError):
                await store.insert(message('changed').to_row())
            assert (await store.get(obs.id)).content[0]['value'] == 'hello'
        finally:
            await database.close()
    asyncio.run(exercise())


def test_batch_creation_conflict_rolls_back(tmp_path):
    async def exercise():
        database = Database(tmp_path)
        await database.start()
        observations, batches = ObservationStore(database), BatchStore(database)
        try:
            obs = message()
            await observations.insert(obs.to_row())
            batch = await close_batch(database,observations,batches,('qq','10000',obs.subject.key),(obs.id,),'quiet')
            with pytest.raises(ValueError):
                await close_batch(database,observations,batches,('qq','10000',obs.subject.key),(obs.id,),'quiet')
            assert (await observations.get(obs.id)).batch_id == batch.id
            assert len(await batches.rows_in_states(('ready',))) == 1
        finally:
            await database.close()
    asyncio.run(exercise())
