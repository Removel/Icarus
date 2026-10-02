import asyncio
from datetime import UTC, datetime
from dataclasses import replace
from apps.argus.src.dao.database import Database
from apps.argus.src.dao.observation_store import ObservationStore
from apps.argus.src.dao.batch_store import BatchStore
from apps.argus.src.handler.qq_handler import QQHandler
from apps.argus.src.service.batch_state import close_batch
from apps.argus.src.evidence.qq.qq_conversation import QQConversationReader


def test_conversation_retains_one_snapshot_when_another_connection_inserts(tmp_path):
    async def exercise():
        database = Database(tmp_path)
        await database.start()
        observations, batches = ObservationStore(database), BatchStore(database)
        def message(n):
            return QQHandler('10000').parse(dict(post_type='message',self_id=10000,
                time=1,message_id=n,message_type='group',group_id=42,user_id=7,message=f'm{n}'))
        try:
            first, later = message(1), message(2)
            await observations.insert(first.to_row())
            batch = await close_batch(database,observations,batches,('qq','10000','qq:group:42'),(first.id,),'quiet')
            original = observations.preceding_rows
            inserted = False
            async def insert_after_watermark(*args, **kwargs):
                nonlocal inserted
                if not inserted:
                    inserted = True
                    await observations.insert(later.to_row())
                return await original(*args, **kwargs)
            observations.preceding_rows = insert_after_watermark
            result = await QQConversationReader(database,observations,batches).evaluate(batch=batch)
            assert result.through_seq == result.human_through_seq == 1
            assert result.following == () and result.current == (first,)
            assert await observations.message_watermark('qq','10000','qq:group:42') == 2
        finally:
            await database.close()
    asyncio.run(exercise())


def test_context_limit_keeps_nearest_preceding_and_first_last_following(tmp_path):
    async def exercise():
        database = Database(tmp_path)
        await database.start()
        observations, batches = ObservationStore(database), BatchStore(database)
        try:
            messages = []
            for n in range(1,34):
                obs = QQHandler('10000').parse(dict(post_type='message',self_id=10000,time=1,
                    message_id=n,message_type='group',group_id=42,user_id=7,message=f'm{n}'))
                await observations.insert(obs.to_row())
                messages.append(obs)
            scope = ('qq','10000','qq:group:42')
            await close_batch(database,observations,batches,scope,tuple(m.id for m in messages[:11]),'quiet')
            batch = await close_batch(database,observations,batches,scope,(messages[11].id,),'quiet')
            result = await QQConversationReader(database,observations,batches).evaluate(batch=batch)
            assert result.preceding == tuple(messages[1:11])
            assert result.following == tuple(messages[12:22]+messages[23:33])
            assert result.current == (messages[11],) and result.truncated
        finally:
            await database.close()
    asyncio.run(exercise())
