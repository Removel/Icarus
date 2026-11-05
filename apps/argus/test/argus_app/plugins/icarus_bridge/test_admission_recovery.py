"""Delivery retries retain the selected Session and only retry proven rejections."""
from types import SimpleNamespace

import pytest

from argus_app.plugins.icarus_bridge.plugin import BridgePlugin


class Store:
    def __init__(self):
        self.round = dict(wake_id='wake', binding_id='binding', session_key='slot',
            observation_ids=['item'], closed=False, session_id=None, task_id=None)
        self.recorded = []
    async def begin_round(self, channel): return self.round.copy()
    async def channel(self, channel):
        return SimpleNamespace(id=channel, title='task', goal='goal', progress='', lifecycle='active',
            version=1, subscriptions={'qq:1:group:2':None}, rule_path='')
    async def observation(self, id): return SimpleNamespace(model_dump=lambda **kw: {'id':id})
    async def set_round_session(self, channel, wake, session):
        self.round['session_id'] = session
        self.recorded.append('selected')
    async def set_round_request(self, channel, wake, payload):
        self.round['wake_payload'] = payload
    async def set_round_submission(self, channel, wake, state, **kwargs):
        self.round['submission'] = dict(admission_state=state, **kwargs)
        self.recorded.append(state)
    async def set_round_delivery(self, channel, wake, session, task):
        self.round.update(session_id=session, task_id=task)
        self.recorded.append('delivered')


@pytest.mark.asyncio
async def test_safe_rejection_is_not_recorded_as_task_delivery():
    store = Store()
    class Client:
        ensure_calls = 0
        async def ensure_session(self, *args):
            self.ensure_calls += 1
            return 'session'
        async def wake(self, **kwargs):
            assert 'selected' in store.recorded
            return dict(task_id='task', status='failed', admission_state='not_admitted',
                attempt=1, retryable=True, error_code='runtime_load_failed')
    client = Client()
    plugin = BridgePlugin('bridge', store, client, 'http://argus', rule_files=SimpleNamespace())
    await plugin.deliver_channel('channel')
    assert store.round['task_id'] is None
    assert store.round['session_id'] == 'session'
    assert store.round['submission']['admission_state'] == 'not_admitted'
    assert 'delivered' not in store.recorded


@pytest.mark.asyncio
async def test_unknown_admission_is_not_resubmitted():
    store = Store()
    store.round.update(session_id='session', submission={'admission_state':'unknown',
        'attempt':1,'retryable':False, 'known_task_id':'task'})
    class Client:
        async def wake(self, **kwargs): raise AssertionError('must query, not submit')
        async def status(self, wake):
            return dict(task_id='task', status='failed', admission_state='unknown',
                attempt=1, retryable=False, commit_state='none', replay_safe=False)
    result = await BridgePlugin('bridge', store, Client(), 'http://argus').deliver_channel('channel')
    assert result['admission_state'] == 'unknown'


@pytest.mark.asyncio
async def test_lost_response_queries_original_wake_after_configuration_change():
    store = Store()
    class Client:
        original = None
        status_calls = 0
        async def ensure_session(self,*args): return 'session'
        async def wake(self,**payload):
            if self.original is None:
                self.original = payload
                raise TimeoutError('response lost after remote claim')
            assert payload == self.original, 'immutable wake changed on retry'
            return dict(task_id='task',status='accepted',admission_state='accepted',attempt=1,retryable=False)
        async def status(self,wake):
            self.status_calls += 1
            return dict(task_id='task',status='accepted',admission_state='accepted',attempt=1,retryable=False)
    client = Client()
    with pytest.raises(TimeoutError):
        await BridgePlugin('bridge',store,client,'http://argus',rule_files=SimpleNamespace(),guide={'version':1}).deliver_channel('channel')
    assert store.round.get('wake_payload') == client.original
    await BridgePlugin('bridge',store,client,'http://changed',rule_files=SimpleNamespace(),guide={'version':2}).deliver_channel('channel')
    assert client.status_calls == 1
    assert store.round['task_id'] == 'task'


def test_store_has_separate_selection_and_submission_operations():
    from argus_app.infrastructure.store import Store as ProductionStore
    assert hasattr(ProductionStore, 'set_round_session')
    assert hasattr(ProductionStore, 'set_round_submission')
