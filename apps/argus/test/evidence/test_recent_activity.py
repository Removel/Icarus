import asyncio
from datetime import UTC, datetime
from apps.argus.test.persistence_fakes import ObservationStore
from apps.argus.test.persistence_fakes import BatchStore
from apps.argus.src.entity.decision import RouteDecision
from apps.argus.src.handler.qq_handler import QQHandler


def test_errors_and_waits_are_not_silence_and_strong_signal_resets(tmp_path):
    async def exercise():
        store = ObservationStore(tmp_path)
        await store.start()
        try:
            batches = BatchStore(store)
            now = datetime.now(UTC)
            for n, verdict in enumerate(('no', None, 'no'), 1):
                obs=QQHandler('10000').parse(dict(post_type='message',self_id=10000,time=1,message_id=n,message_type='group',group_id=42,user_id=7,message='chat'))
                await store.insert(obs)
                batch=await batches.close_batch(('qq','10000',obs.subject.key),(obs.id,),'quiet')
                await batches.claim_batch(batch.id)
                await batches.save_decision(batch.id,decision=RouteDecision('silence','not_required'),run_status='failed' if verdict is None else 'completed',trace={},judged_at=now,intervention=verdict,judged_through_seq=n)
            result = await batches.recent_activity('qq','10000','qq:group:42',through_seq=3,now=now)
            assert result.consecutive_no_action == 2
            obs=QQHandler('10000').parse(dict(post_type='message',self_id=10000,time=1,message_id=4,message_type='group',group_id=42,user_id=7,message='icarus'))
            await store.insert(obs,is_strong_signal=True)
            result = await batches.recent_activity('qq','10000','qq:group:42',through_seq=4,now=now)
            assert result.consecutive_no_action == 0 and result.reset_seq == 4
        finally:
            await store.close()
    asyncio.run(exercise())
