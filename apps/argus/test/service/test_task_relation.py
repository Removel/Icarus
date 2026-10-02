import asyncio
from typesafe_sdk import SystemOneResponse, Usage, ChoiceAnswer
from apps.argus.test.persistence_fakes import ObservationStore, SessionLink
from apps.argus.test.persistence_fakes import BatchStore
from apps.argus.src.handler.qq_handler import QQHandler
from apps.argus.test.persistence_fakes import QQService


def test_existing_task_requires_relation_judgment_before_steer(tmp_path):
    async def exercise():
        store = ObservationStore(tmp_path)
        await store.start()
        batches = BatchStore(store)
        obs = QQHandler('10000').parse(dict(
            post_type='message', self_id=10000, time=1, message_id=73,
            message_type='private', user_id=7, message='补充一下',
        ))
        await store.insert(obs)
        batch = await batches.close_batch(('qq', '10000', obs.subject.key), (obs.id,), 'direct')
        await store.save_link(SessionLink('qq', '10000', obs.subject.key, str(tmp_path), 'key', 's'))
        class Gateway:
            async def session_get(self, *args):
                return dict(session_id='s', workspace_key='key', lifecycle='running', active_task_ids=['t'])
            async def task_get_status(self, path, sid, task):
                return dict(workspace_key='key',session_id=sid,task_id=task,lifecycle='running')
            async def session_get_history(self, *args, **kwargs):
                return {'records': [dict(task_id='t', type='user.message', payload={'text': '原问题'})]}
        class Judge:
            def __init__(self):
                self.calls = []
                self.choice = 'steer'
            async def ask(self, *, state, questions):
                self.calls.append((state, questions))
                return SystemOneResponse(model='fake', usage=Usage(), answers={
                    key: ChoiceAnswer(choice='yes' if key=='intervene' else self.choice, confidence=1., probabilities={('yes' if key=='intervene' else self.choice):1.}) for key in questions})
        judge = Judge()
        service = QQService(store, judge, Gateway(), workspace_path=str(tmp_path))
        result = await service.evaluate_batch(batch)
        assert result.output.action == 'steer' and result.output.target_task_id == 't'
        assert judge.calls[0][0]['active_task_messages'] == ['原问题']
        judge.choice = 'wait'
        assert (await service.evaluate_batch(batch)).output.action == 'wait'
        await store.close()
    asyncio.run(exercise())
