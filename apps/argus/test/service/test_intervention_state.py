import asyncio
from typesafe_sdk import ChoiceAnswer, SystemOneResponse, Usage
from apps.argus.test.persistence_fakes import ObservationStore, SessionLink
from apps.argus.test.persistence_fakes import BatchStore
from apps.argus.src.handler.qq_handler import QQHandler
from apps.argus.test.persistence_fakes import QQService


class Gateway:
    def __init__(self, active=()):
        self.active = list(active)
    async def session_get(self, workspace_path, session_id):
        return dict(session_id=session_id, workspace_key='key', lifecycle='running',
                    active_task_ids=self.active)
    async def task_get_status(self, path, sid, task):
        return dict(workspace_key='key', session_id=sid, task_id=task, lifecycle='running')
    async def session_get_history(self, *args, **kwargs):
        return {'records': []}


class Judge:
    def __init__(self, choice='no'):
        self.choice = choice
        self.states = []
    async def ask(self, *, state, questions):
        self.states.append(state)
        key = next(iter(questions))
        return SystemOneResponse(model='fake', usage=Usage(), answers={
            key: ChoiceAnswer(choice=self.choice, confidence=1., probabilities={self.choice: 1.})})


def group_message(message_id, text):
    return QQHandler('10000').parse(dict(
        post_type='message', self_id=10000, time=1, message_id=message_id,
        message_type='group', group_id=42, user_id=7, message=text))


def test_intervention_state_includes_ongoing_session_facts(tmp_path):
    async def exercise():
        store = ObservationStore(tmp_path)
        await store.start()
        obs = group_message(80, '这块谁来处理一下')
        await store.insert(obs)
        batch = await BatchStore(store).close_batch(('qq', '10000', obs.subject.key), (obs.id,), 'quiet')
        await store.save_link(SessionLink('qq', '10000', obs.subject.key, str(tmp_path), 'key', 's'))
        judge = Judge()
        service = QQService(store, judge, Gateway(active=['t']), workspace_path=str(tmp_path))
        await service.process_batch(batch.id)
        state = judge.states[0]
        assert state['current_session'] is True
        assert state['active_task_count'] == 1
        await store.close()
    asyncio.run(exercise())


def test_intervention_state_reports_absent_session(tmp_path):
    async def exercise():
        store = ObservationStore(tmp_path)
        await store.start()
        obs = group_message(81, '随便聊聊')
        await store.insert(obs)
        batch = await BatchStore(store).close_batch(('qq', '10000', obs.subject.key), (obs.id,), 'quiet')
        judge = Judge()
        service = QQService(store, judge, Gateway(), workspace_path=str(tmp_path))
        await service.process_batch(batch.id)
        state = judge.states[0]
        assert state['current_session'] is False and state['active_task_count'] == 0
        await store.close()
    asyncio.run(exercise())


def test_transcript_marks_bot_messages():
    from apps.argus.src.evidence.qq.qq_transcript import transcript
    own = QQHandler('10000').parse(dict(
        post_type='message_sent', self_id=10000, time=1, message_id=82,
        message_type='group', group_id=42, user_id=10000, message='我查到了'))
    human = group_message(83, '谢谢')
    text = transcript((own, human))
    assert text.splitlines()[0].startswith('[机器人]')
    assert not text.splitlines()[1].startswith('[机器人]')
