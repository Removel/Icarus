import asyncio
from apps.argus.src.dao.observation_store import ObservationStore
from apps.argus.src.dao.batch_store import BatchStore
from apps.argus.src.handler.qq_handler import QQHandler


def test_restart_recovers_same_batch_but_never_resends_uncertain_operation(tmp_path):
    async def exercise():
        store=ObservationStore(tmp_path);await store.start();batches=BatchStore(store)
        ids=[]
        for n in (1,2):
            obs=QQHandler('10000').parse(dict(post_type='message',self_id=10000,time=1,message_id=n,message_type='private',user_id=7,message='hi'))
            await store.insert(obs)
            batch=await batches.close_batch(('qq','10000',obs.subject.key),(obs.id,),'direct');ids.append(batch.id)
            await batches.claim_batch(batch.id)
        await batches.transition(ids[1],('judging',),status='decided')
        await batches.begin_submission(ids[1],'session.submit',{'prompt':'hi'},ids[1]+':submit')
        await store.close();await store.start()
        await batches.reset_interrupted()
        assert await batches.recover_batches()==(ids[0],)
        assert (await batches.record(ids[1]))['status']=='submission_unknown'
        assert (await batches.get(ids[0])).messages[0].source.source_message_id=='1'
        await store.close()
    asyncio.run(exercise())
