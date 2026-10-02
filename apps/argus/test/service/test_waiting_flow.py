import asyncio
from datetime import UTC, datetime, timedelta
from typesafe_sdk import ChoiceAnswer, SystemOneResponse, Usage
from apps.argus.test.persistence_fakes import ObservationStore, SessionLink
from apps.argus.test.persistence_fakes import BatchStore
from apps.argus.src.handler.qq_handler import QQHandler
from apps.argus.test.persistence_fakes import QQService


class WorkGateway:
    connected = True
    def __init__(self):
        self.running = True
        self.submissions = []
        self.steers = []
        self.sequence = 1
    async def session_get(self, path, sid):
        return dict(workspace_key='key',session_id=sid,lifecycle='running' if self.running else 'ready',active_task_ids=['t'] if self.running else [],queued_task_count=0,pending_event_count=0,pending_plugin_event_count=0,background_work_count=0)
    async def task_get_status(self, path, sid, task):
        return dict(workspace_key='key',session_id=sid,task_id=task,lifecycle='running' if self.running else 'completed')
    async def session_get_history(self, path, sid, **kwargs):
        return dict(records=[dict(workspace_key='key',session_id=sid,task_id='t',type='user.message',payload={'text':'原问题'},sequence=1)],history_cursor=self.sequence,has_more=False)
    async def session_submit(self, **params):
        self.submissions.append(params)
        return dict(task_id='new-task')
    async def session_steer(self, **params):
        self.steers.append(params)
        return dict(status='accepted',task_id='t')


class Choices:
    def __init__(self):
        self.calls = []
        self.intervene = 'yes'
        self.relation = 'wait'
    async def ask(self, *, state, questions):
        self.calls.append((state,questions))
        answers = {}
        for key in questions:
            choice = self.intervene if key == 'intervene' else self.relation
            answers[key] = ChoiceAnswer(choice=choice,confidence=1.,probabilities={choice:1.})
        return SystemOneResponse(model='fake',usage=Usage(),answers=answers)


def test_waiting_is_not_silence_and_idle_rechecks_without_new_message(tmp_path):
    async def exercise():
        store=ObservationStore(tmp_path)
        await store.start()
        try:
            await store.save_link(SessionLink('qq','10000','qq:private:7',str(tmp_path),'key','s'))
            obs=QQHandler('10000').parse(dict(post_type='message',self_id=10000,time=1,message_id=1,message_type='private',user_id=7,message='新的问题'))
            await store.insert(obs,is_strong_signal=True)
            batches=BatchStore(store)
            batch=await batches.close_batch(('qq','10000',obs.subject.key),(obs.id,),'direct')
            gateway,judge=WorkGateway(),Choices()
            now=datetime.now(UTC)
            service=QQService(store,judge,gateway,workspace_path=str(tmp_path),clock=lambda:now)
            await service.process_batch(batch.id)
            record=await batches.record(batch.id)
            assert record['status']=='waiting'
            assert not gateway.submissions and not gateway.steers
            count=len(judge.calls)
            assert not await service.waiting_changed(batch.id,now=now+timedelta(seconds=5))
            assert len(judge.calls)==count
            gateway.running=False
            gateway.sequence=2
            assert await service.waiting_changed(batch.id,now=now+timedelta(seconds=10))
            await service.process_batch(batch.id)
            assert (await batches.record(batch.id))['status']=='submitted'
            assert len(gateway.submissions)==1
            assert '新的问题' in gateway.submissions[0]['prompt']
            assert len((await batches.record(batch.id))['trace'])==2
        finally:
            await store.close()
    asyncio.run(exercise())
