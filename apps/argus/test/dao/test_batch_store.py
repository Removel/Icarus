import asyncio
from datetime import UTC, datetime
import pytest
from apps.argus.test.persistence_fakes import ObservationStore
from apps.argus.test.persistence_fakes import BatchStore
from apps.argus.src.handler.qq_handler import QQHandler


def make_message(n):
    return QQHandler('10000').parse(dict(post_type='message',self_id=10000,time=1700000000,
        message_id=n,message_type='group',group_id=42,user_id=7,message=f'message {n}'))


def test_batch_members_are_atomic_stable_and_not_reassigned(tmp_path):
    async def exercise():
        store=ObservationStore(tmp_path);await store.start()
        messages=[make_message(n) for n in (1,2,3)]
        for m in messages: await store.insert(m)
        batches=BatchStore(store)
        batch=await batches.close_batch(('qq','10000','qq:group:42'),tuple(m.id for m in messages[:2]),'quiet')
        assert batch.messages==tuple(messages[:2])
        assert await store.unassigned(('qq','10000','qq:group:42'),20)==(messages[2],)
        with pytest.raises(ValueError):
            await batches.close_batch(('qq','10000','qq:group:42'),(messages[0].id,),'quiet')
        await store.close();await store.start()
        assert (await batches.get(batch.id)).messages==tuple(messages[:2])
        assert await batches.claim_batch(batch.id)
        assert not await batches.claim_batch(batch.id)
        await store.close()
    asyncio.run(exercise())
