import asyncio
from sqlalchemy import text


def test_database_lifecycle_and_version(tmp_path):
    from apps.argus.src.dao.database import Database
    async def exercise():
        database = Database(tmp_path)
        assert not database.is_running
        await database.start()
        assert database.is_running and await database.is_available()
        async with database.session() as session:
            assert await session.scalar(text('PRAGMA user_version')) == 3
        await database.close()
        assert not database.is_running and not await database.is_available()
    asyncio.run(exercise())


def test_business_mapping_copies_json_and_roundtrips():
    from apps.argus.src.handler.qq_handler import QQHandler
    from apps.argus.src.entity.qq_observation import QQObservation
    obs = QQHandler('10000').parse(dict(post_type='message', self_id=10000,
        time=1700000000, message_id=1, message_type='group', group_id=42,
        user_id=7, message='hello'))
    row = obs.to_row(is_strong_signal=True)
    assert QQObservation.from_row(row) == obs
    restored = QQObservation.from_row(row)
    row.content[0]['value'] = 'changed'
    assert restored.content[0].value == 'hello'
