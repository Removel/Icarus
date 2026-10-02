import asyncio
from apps.argus.test.persistence_fakes import ObservationStore
from apps.argus.test.persistence_fakes import BatchStore
from apps.argus.src.handler.qq_handler import QQHandler
from apps.argus.test.persistence_fakes import QQService
from apps.argus.src.entity.decision import RouteDecision


def test_unexpected_delivery_error_is_recorded_without_escaping_consumer(tmp_path, caplog):
    async def exercise():
        store=ObservationStore(tmp_path);await store.start();batches=BatchStore(store)
        obs=QQHandler('10000').parse(dict(post_type='message',self_id=10000,time=1,message_id=91,
            message_type='private',user_id=7,message='hello'))
        await store.insert(obs)
        batch=await batches.close_batch(('qq','10000',obs.subject.key),(obs.id,),'direct')
        await batches.transition(batch.id,('ready',),status='decided',route=dict(action='submit',reason='test',target_task_id=None))
        service=QQService(store,object(),object(),workspace_path=str(tmp_path))
        async def broken(batch,decision):
            await batches.begin_submission(batch.id,'session.submit',{'session_id':'s'},batch.id+':submit')
            raise RuntimeError('sensitive message not logged')
        service.apply_decision=broken
        await service.process_batch(batch.id)
        assert (await batches.record(batch.id))['status']=='submission_unknown'
        assert 'sensitive message' not in caplog.text
        await store.close()
    asyncio.run(exercise())
