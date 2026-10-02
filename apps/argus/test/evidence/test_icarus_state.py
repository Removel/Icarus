import asyncio
from datetime import UTC, datetime
from apps.argus.test.persistence_fakes import ObservationStore, SessionLink
from apps.argus.src.entity.decision import ObservationBatch
from apps.argus.src.handler.qq_handler import QQHandler
from apps.argus.test.persistence_fakes import IcarusStateReader
from apps.argus.src.clients.icarus_client import GatewayBusinessError


def test_queued_and_running_are_not_selected_by_id_order(tmp_path):
    async def exercise():
        store = ObservationStore(tmp_path)
        await store.start()
        try:
            await store.save_link(SessionLink('qq','10000','qq:group:42',str(tmp_path),'key','s'))
            obs = QQHandler('10000').parse(dict(post_type='message',self_id=10000,time=1,message_id=1,message_type='group',group_id=42,user_id=7,message='question'))
            batch = ObservationBatch('b','qq','10000',obs.subject.key,(obs,),obs.received_at,datetime.now(UTC))
            class Gateway:
                async def session_get(self, *_):
                    return dict(workspace_key='key',session_id='s',lifecycle='running',active_task_ids=['queued','running'],queued_task_count=1,pending_event_count=0,pending_plugin_event_count=0,background_work_count=0)
                async def task_get_status(self, path, sid, task):
                    if task == 'queued':
                        raise GatewayBusinessError('task_status_unavailable','missing')
                    return dict(workspace_key='key',session_id='s',task_id=task,lifecycle='running')
                async def session_get_history(self, *_, **kwargs):
                    return dict(records=[dict(task_id='running',type='user.message',payload={'text':'original'},sequence=1)],history_cursor=1,has_more=False)
            snapshot = await IcarusStateReader(store,Gateway(),str(tmp_path)).evaluate(batch=batch)
            assert snapshot.tasks[0].status_known is False
            assert snapshot.tasks[0].lifecycle is None
            assert snapshot.tasks[1].lifecycle == 'running'
            assert snapshot.queued_task_count == 1
            assert snapshot.stable
        finally:
            await store.close()
    asyncio.run(exercise())


def test_session_change_during_history_read_is_not_confirmed_idle(tmp_path):
    async def exercise():
        store=ObservationStore(tmp_path)
        await store.start()
        try:
            await store.save_link(SessionLink('qq','10000','qq:group:42',str(tmp_path),'key','s'))
            obs=QQHandler('10000').parse(dict(post_type='message',self_id=10000,time=1,message_id=1,message_type='group',group_id=42,user_id=7,message='question'))
            batch=ObservationBatch('b','qq','10000',obs.subject.key,(obs,),obs.received_at,datetime.now(UTC))
            class Gateway:
                calls=0
                async def session_get(self,*_):
                    self.calls+=1
                    return dict(workspace_key='key',session_id='s',lifecycle='ready' if self.calls==1 else 'running',active_task_ids=[] if self.calls==1 else ['new'],queued_task_count=0,pending_event_count=0,pending_plugin_event_count=0,background_work_count=0)
                async def session_get_history(self,*_,**kwargs):
                    return dict(records=[],history_cursor=0,has_more=False)
            snapshot=await IcarusStateReader(store,Gateway(),str(tmp_path)).evaluate(batch=batch)
            from apps.argus.src.strategy.work_state import confirmed_idle
            assert not snapshot.stable and not confirmed_idle(snapshot)
        finally:
            await store.close()
    asyncio.run(exercise())


def test_own_task_request_uses_exact_delivered_payload_not_display_text(tmp_path):
    async def exercise():
        from apps.argus.test.application.conversation_fakes import make_flow,receive,drain
        flow=await make_flow(tmp_path)
        try:
            obs=await receive(flow,text='生成A表，禁止B表',private=True)
            await drain(flow, settle=True)
            flow.gateway.records[0]['payload']['text']='正在执行的任务（仅展示文本）'
            batch=await flow.batches.get((await flow.store.status_details(obs.id))['batch_id'])
            snapshot=await flow.service.work_reader.evaluate(batch=batch)
            requests=[r['payload']['text'] for r in snapshot.history_records if r['type']=='user.message']
            assert any('生成A表，禁止B表' in text for text in requests)
        finally:
            await flow.store.close()
    asyncio.run(exercise())
