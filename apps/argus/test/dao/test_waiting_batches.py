import asyncio
from datetime import UTC, datetime, timedelta
from apps.argus.test.persistence_fakes import ObservationStore
from apps.argus.test.persistence_fakes import BatchStore
from apps.argus.src.entity.decision import RouteDecision
from apps.argus.src.handler.qq_handler import QQHandler


def test_waiting_head_survives_restart_blocks_following_and_keeps_runs(tmp_path):
    async def exercise():
        store = ObservationStore(tmp_path)
        await store.start()
        try:
            batches = BatchStore(store)
            created = []
            for n in (1, 2):
                obs = QQHandler('10000').parse(dict(post_type='message',self_id=10000,time=1,message_id=n,message_type='private',user_id=7,message='hi'))
                await store.insert(obs, is_strong_signal=True)
                created.append(await batches.close_batch(('qq','10000',obs.subject.key),(obs.id,),'direct'))
            first, second = created
            now = datetime.now(UTC)
            assert await batches.claim_batch(first.id)
            assert await batches.save_decision(first.id, decision=RouteDecision('wait','busy'), run_status='completed', trace={'run_id':'r1','nodes':[]}, judged_at=now, intervention='yes', judged_through_seq=2, next_check_at=now+timedelta(seconds=5), wait_fingerprint='a')
            assert await batches.recover_batches() == ()
            assert await batches.waiting_heads() == (first.id,)
            await store.close()
            await store.start()
            assert await batches.claim_batch(first.id)
            await batches.reset_interrupted()
            assert (await batches.record(first.id))['status'] == 'waiting'
            assert await batches.claim_batch(first.id)
            await batches.save_decision(first.id, decision=RouteDecision('silence','not_required'), run_status='completed', trace={'run_id':'r2','nodes':[]}, judged_at=now, intervention='no', judged_through_seq=2)
            row = await batches.record(first.id)
            assert [r['run_id'] for r in row['trace']] == ['r1','r2']
            assert await batches.recover_batches() == (second.id,)
        finally:
            await store.close()
    asyncio.run(exercise())


def test_duplicate_keeps_original_derived_signal(tmp_path):
    async def exercise():
        store = ObservationStore(tmp_path)
        await store.start()
        try:
            obs = QQHandler('10000').parse(dict(post_type='message',self_id=10000,time=1,message_id=1,message_type='group',group_id=42,user_id=7,message='name'))
            assert await store.insert(obs, is_strong_signal=False)
            assert not await store.insert(obs, is_strong_signal=True)
            assert await store.latest_strong_signal('qq','10000',obs.subject.key) == 0
        finally:
            await store.close()
    asyncio.run(exercise())
