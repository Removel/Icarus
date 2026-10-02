import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from apps.argus.test.persistence_fakes import ObservationStore
from apps.argus.test.persistence_fakes import BatchStore
from apps.argus.test.persistence_fakes import BatchScheduler
from apps.argus.src.handler.qq_handler import QQHandler


def test_queue_full_batches_remain_durable_after_private_collection(tmp_path):
    async def exercise():
        store=ObservationStore(tmp_path);await store.start();batches=BatchStore(store)
        now = datetime.now(UTC).timestamp()
        scheduler=BatchScheduler(store,batches,capacity=1,clock=lambda:now)
        for n in (1,2):
            obs=QQHandler('10000').parse(dict(post_type='message',self_id=10000,time=1,
                message_id=n,message_type='private',user_id=7,message='hi'))
            obs = replace(obs,received_at=datetime.fromtimestamp(now,UTC))
            await store.insert(obs);await scheduler.offer(obs)
            now += 6
            await scheduler.check_due()
        ids=await batches.recover_batches();assert len(ids)==1  # Only the scope head may execute.
        first=await scheduler.ready.get();scheduler.done(first)
        await batches.transition(first,('ready',),status='finished')
        await scheduler.replenish()
        second=await scheduler.ready.get();assert second!=first
        scheduler.done(second);await store.close()
    asyncio.run(exercise())
