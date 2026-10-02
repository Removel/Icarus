"""Only external services and the clock are fake; persistence and policies are real."""
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typesafe_sdk import ChoiceAnswer, SystemOneResponse, Usage
from apps.argus.test.persistence_fakes import ObservationStore, SessionLink
from apps.argus.test.persistence_fakes import BatchStore
from apps.argus.src.handler.qq_handler import QQHandler
from apps.argus.test.persistence_fakes import QQService
from apps.argus.test.persistence_fakes import BatchScheduler
from apps.argus.src.evidence.qq.qq_signals import is_strong_signal


class FakeClock:
    def __init__(self):
        self.now = datetime(2026,10,1,tzinfo=UTC)
    def advance(self, seconds):
        self.now += timedelta(seconds=seconds)


class FakeGateway:
    connected = True
    def __init__(self):
        self.tasks = {}
        self.records = []
        self.submissions, self.steers = [], []
        self.sequence = 0
        self.reject_steer = False
    def set_running(self, task_id, *, request):
        self.tasks[task_id] = 'running'
        self.sequence += 1
        self.records.append(dict(workspace_key='key',session_id='s',task_id=task_id,type='user.message',payload={'text':request},sequence=self.sequence))
    def finish(self, task_id):
        self.tasks[task_id] = 'completed'
        self.sequence += 1
        self.records.append(dict(workspace_key='key',session_id='s',task_id=task_id,type='task.finished',payload={'status':'completed'},sequence=self.sequence))
    async def session_get(self, path, sid):
        active = [id for id, status in self.tasks.items() if status in {'running','accepted'}]
        return dict(workspace_key='key',session_id=sid,lifecycle='running' if active else 'ready',active_task_ids=active,
            queued_task_count=sum(v=='accepted' for v in self.tasks.values()),pending_event_count=0,pending_plugin_event_count=0,background_work_count=0)
    async def task_get_status(self, path, sid, task):
        return dict(workspace_key='key',session_id=sid,task_id=task,lifecycle=self.tasks[task])
    async def session_get_history(self, path, sid, **kwargs):
        after = kwargs.get('after_sequence',0)
        return dict(records=[r for r in self.records if r['sequence']>after],history_cursor=self.sequence,has_more=False)
    async def session_submit(self, **params):
        self.submissions.append(params)
        id = f'new-{len(self.submissions)}'
        self.set_running(id,request=params['prompt'])
        return dict(task_id=id,queue_position=0)
    async def session_steer(self, **params):
        self.steers.append(params)
        if self.reject_steer:
            self.finish(params['task_id'])
            return dict(status='already_finished',task_id=params['task_id'])
        self.sequence += 1
        self.records.append(dict(workspace_key='key',session_id='s',task_id=params['task_id'],type='user.correction',payload={'text':params['prompt']},sequence=self.sequence))
        return dict(status='accepted',task_id=params['task_id'])


class FakeJudge:
    def __init__(self):
        self.choices = {'intervene':'yes','relation':'wait'}
        self.calls = []
    @property
    def call_count(self):
        return len(self.calls)
    def set_choices(self, **choices):
        self.choices.update(choices)
    async def ask(self, *, state, questions):
        self.calls.append((state,questions))
        return SystemOneResponse(model='fake',usage=Usage(),answers={key:ChoiceAnswer(choice=self.choices[key],confidence=1.,probabilities={self.choices[key]:1.}) for key in questions})


@dataclass
class Flow:
    store: object
    batches: object
    scheduler: object
    service: object
    gateway: object
    judge: object
    clock: object
    received: int = 0


async def make_flow(tmp_path):
    store = ObservationStore(tmp_path)
    await store.start()
    await store.save_link(SessionLink('qq','10000','qq:private:7',str(tmp_path),'key','s'))
    await store.save_link(SessionLink('qq','10000','qq:group:42',str(tmp_path),'key','s'))
    clock, gateway, judge = FakeClock(), FakeGateway(), FakeJudge()
    batches = BatchStore(store)
    service = QQService(store,judge,gateway,workspace_path=str(tmp_path),clock=lambda:clock.now,bot_names=('icarus',))
    scheduler = BatchScheduler(store,batches,clock=lambda:clock.now.timestamp(),waiting_changed=service.waiting_changed,bot_names=('icarus',))
    return Flow(store,batches,scheduler,service,gateway,judge,clock)


async def receive(flow, *, text, sender_id='7', private=False, at=False):
    from dataclasses import replace
    flow.received += 1
    message = [{'type':'text','data':{'text':text}}]
    if at:
        message.append({'type':'at','data':{'qq':'10000'}})
    obs = QQHandler('10000').parse(dict(post_type='message',self_id=10000,time=flow.clock.now.timestamp(),message_id=flow.received,
        message_type='private' if private else 'group',group_id=42,user_id=int(sender_id),target_id=7,message=message))
    obs = replace(obs,received_at=flow.clock.now)
    flow.service.notify = flow.scheduler.notify
    await flow.service.receive(obs)
    await flow.scheduler.check_due()
    return obs


async def drain(flow, *, settle=False):
    if settle:
        # Explicitly let the collection window expire in scenarios testing later stages.
        flow.clock.advance(flow.scheduler.policy.quiet_seconds)
        await flow.scheduler.check_due()
    while not flow.scheduler.ready.empty():
        id = await flow.scheduler.ready.get()
        try:
            await flow.service.process_batch(id)
        finally:
            flow.scheduler.done(id)
        await flow.scheduler.replenish()
