import asyncio
from apps.argus.src.dao.observation_store import ObservationStore
from apps.argus.src.dao.batch_store import BatchStore
from apps.argus.src.service.batch_scheduler import BatchScheduler
from apps.argus.src.handler.qq_handler import QQHandler


def test_only_scope_head_is_scheduled_and_query_is_bounded(tmp_path):
    async def exercise():
        store=ObservationStore(tmp_path);await store.start();batches=BatchStore(store)
        ids=[]
        for n,peer in [(1,7),(2,7),(3,8)]:
            obs=QQHandler('10000').parse(dict(post_type='message',self_id=10000,time=1,message_id=n,message_type='private',user_id=peer,message='hi'))
            await store.insert(obs)
            ids.append((await batches.close_batch(('qq','10000',obs.subject.key),(obs.id,),'direct')).id)
        assert await batches.recover_batches(limit=1)==(ids[0],)
        scheduler=BatchScheduler(store,batches,capacity=3)
        await scheduler.replenish()
        assert scheduler.scheduled=={ids[0],ids[2]}
        await batches.transition(ids[0],('ready',),status='decided')
        scheduler.done(await scheduler.ready.get())
        await scheduler.replenish()
        assert ids[1] not in scheduler.scheduled
        await batches.transition(ids[0],('decided',),status='submitted')
        await scheduler.replenish()
        assert ids[1] in scheduler.scheduled
        await store.close()
    asyncio.run(exercise())
