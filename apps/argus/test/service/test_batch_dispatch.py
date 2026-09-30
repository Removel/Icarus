import asyncio
from datetime import UTC,datetime
from apps.argus.src.dao.observation_store import ObservationStore
from apps.argus.src.dao.batch_store import BatchStore
from apps.argus.src.handler.qq_handler import QQHandler
from apps.argus.src.service.qq_service import QQService
from apps.argus.src.entity.decision import RouteDecision
from apps.argus.src.clients.icarus_client import GatewayTransportError,GatewayBusinessError


class Gateway:
    def __init__(self,result):self.result=result;self.calls=[];self.sessions={}
    async def session_get(self,workspace_path,session_id):
        return dict(session_id=session_id,workspace_key='k',lifecycle='running',active_task_ids=['t'])
    async def task_get_status(self,*args):return {'lifecycle':'running'}
    async def session_steer(self,**params):
        self.calls.append(('steer',params))
        if isinstance(self.result,Exception):raise self.result
        return self.result
    async def session_submit(self,**params):self.calls.append(('submit',params));return {'task_id':'new'}


def test_malformed_receipt_is_unknown_and_identity_is_saved(tmp_path):
    async def exercise():
        from apps.argus.src.dao.observation_store import SessionLink
        store=ObservationStore(tmp_path);await store.start();batches=BatchStore(store)
        obs=QQHandler('10000').parse(dict(post_type='message',self_id=10000,time=1,message_id=70,
            message_type='private',user_id=7,message='hi'))
        await store.insert(obs)
        batch=await batches.close_batch(('qq','10000',obs.subject.key),(obs.id,),'direct')
        await batches.transition(batch.id,('ready',),status='decided')
        await store.save_link(SessionLink('qq','10000',obs.subject.key,str(tmp_path),'k','s'))
        gateway=Gateway(None)
        await QQService(store,object(),gateway,workspace_path=str(tmp_path)).apply_decision(batch,RouteDecision('steer','supplement','t'))
        record=await batches.record(batch.id)
        assert record['status']=='submission_unknown'
        assert (record['workspace_path'],record['workspace_key'],record['session_id'])==(str(tmp_path),'k','s')
        await store.close()
    asyncio.run(exercise())


def test_missing_steer_task_is_reconciled_with_session_snapshot(tmp_path):
    async def exercise():
        from apps.argus.src.dao.observation_store import SessionLink
        store=ObservationStore(tmp_path);await store.start();batches=BatchStore(store)
        obs=QQHandler('10000').parse(dict(post_type='message',self_id=10000,time=1,message_id=71,
            message_type='private',user_id=7,message='hi'))
        await store.insert(obs)
        batch=await batches.close_batch(('qq','10000',obs.subject.key),(obs.id,),'direct')
        await batches.transition(batch.id,('ready',),status='decided')
        await store.save_link(SessionLink('qq','10000',obs.subject.key,str(tmp_path),'k','s'))
        gateway=Gateway(None)
        async def absent(*_): raise GatewayBusinessError('task_status_unavailable','missing')
        async def idle(*_): return dict(session_id='s',workspace_key='k',lifecycle='ready',active_task_ids=[],queued_task_count=0)
        gateway.task_get_status=absent;gateway.session_get=idle
        await QQService(store,object(),gateway,workspace_path=str(tmp_path)).apply_decision(batch,RouteDecision('steer','supplement','t'))
        assert [c[0] for c in gateway.calls]==['submit']
        assert (await batches.record(batch.id))['status']=='submitted'
        await store.close()
    asyncio.run(exercise())


def test_steer_negative_receipt_allows_one_submit_but_unknown_does_not(tmp_path):
    async def exercise():
        from apps.argus.src.dao.observation_store import SessionLink
        store=ObservationStore(tmp_path);await store.start();batches=BatchStore(store)
        for n,result,expected in [(1,{'status':'not_running'},'submitted'),(2,GatewayTransportError('lost'),'submission_unknown'),(3,{'status':'accepted','task_id':'t'},'submitted')]:
            obs=QQHandler('10000').parse(dict(post_type='message',self_id=10000,time=1,message_id=n,
                message_type='private',user_id=7,message='supplement'))
            await store.insert(obs)
            batch=await batches.close_batch(('qq','10000',obs.subject.key),(obs.id,),'direct')
            await batches.transition(batch.id,('ready',),status='decided')
            await store.save_link(SessionLink('qq','10000',obs.subject.key,str(tmp_path),'k','s'))
            gateway=Gateway(result)
            service=QQService(store,object(),gateway,workspace_path=str(tmp_path))
            await service.apply_decision(batch,RouteDecision('steer','supplement','t'))
            assert (await batches.record(batch.id))['status']==expected
            assert [c[0] for c in gateway.calls]==(['steer','submit'] if n==1 else ['steer'])
            if n==1:assert gateway.calls[0][1]['submission_id']!=gateway.calls[1][1]['submission_id']
            await service.apply_decision(batch,RouteDecision('steer','supplement','t'))
            assert len(gateway.calls)==(2 if n==1 else 1)
        await store.close()
    asyncio.run(exercise())
