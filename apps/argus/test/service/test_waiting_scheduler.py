import asyncio
from datetime import UTC, datetime, timedelta
from apps.argus.test.persistence_fakes import ObservationStore
from apps.argus.test.persistence_fakes import BatchStore
from apps.argus.src.entity.decision import RouteDecision
from apps.argus.src.handler.qq_handler import QQHandler
from apps.argus.test.persistence_fakes import BatchScheduler


def test_unchanged_waiting_checks_only_at_deadline(tmp_path):
    async def exercise():
        store=ObservationStore(tmp_path)
        await store.start()
        try:
            batches=BatchStore(store)
            obs=QQHandler('10000').parse(dict(post_type='message',self_id=10000,time=1,message_id=1,message_type='private',user_id=7,message='hi'))
            await store.insert(obs)
            batch=await batches.close_batch(('qq','10000',obs.subject.key),(obs.id,),'direct')
            now=datetime.now(UTC)
            await batches.claim_batch(batch.id)
            await batches.save_decision(batch.id,decision=RouteDecision('wait','busy'),run_status='completed',trace={},next_check_at=now+timedelta(seconds=5))
            checks=[]
            async def unchanged(id,*,now):
                checks.append(id)
                await batches.postpone_check(id,next_check_at=now+timedelta(seconds=5))
                return False
            scheduler=BatchScheduler(store,batches,waiting_changed=unchanged)
            await scheduler.check_due(now.timestamp()+4)
            assert not checks
            await scheduler.check_due(now.timestamp()+5)
            assert checks==[batch.id] and scheduler.ready.empty()
            await scheduler.check_due(now.timestamp()+6)
            assert checks==[batch.id]
        finally:
            await store.close()
    asyncio.run(exercise())
