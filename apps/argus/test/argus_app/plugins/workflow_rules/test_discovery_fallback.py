import asyncio
import inspect

import pytest
import pytest_asyncio

from argus_app.contracts.capabilities import RuleResponse
from argus_app.contracts.models import DiscoveryDecision, DomainError
from argus_app.infrastructure.store import Store, observed_items
from argus_app.plugins.workflow_rules.plugin import WorkflowRules
from argus_app.rule_sdk import RuleFiles
from icarus.core.capabilities import CapabilityStatus


@pytest_asyncio.fixture
async def store(database_url):
    value = Store(database_url)
    await value.start()
    try:
        yield value
    finally:
        await value.close()


class Engine:
    def __init__(self, direct=None):
        self.direct, self.calls = direct, 0

    async def run(self, source, event, context, call):
        self.calls += 1
        if self.direct is not None:
            return self.direct
        value = await call('semantic', {'mode': 'discovery', 'text': event['content']['text']})
        return {**value['discovery'], 'reason': value['reason']}


class Fallback:
    def __init__(self, action='new', error=None, channel_id=None):
        self.action, self.error, self.channel_id, self.calls = action, error, channel_id, []

    async def __call__(self, text, candidates):
        self.calls.append((text, candidates))
        if self.error:
            raise self.error
        return DiscoveryDecision(action=self.action, reason='one-shot result', channel_id=self.channel_id)


def plugin(store, tmp_path, fallback, *, result=None, engine=None):
    assert 'discovery_fallback' in inspect.signature(WorkflowRules).parameters, 'fallback orchestration is missing'
    value = WorkflowRules('workflow-rules', store, engine or Engine(), RuleFiles(tmp_path),
                          discovery_fallback=fallback)
    value.published = []
    async def publish(event):
        value.published.append(event)
    value.bind_publisher(publish)
    async def semantic(request):
        if isinstance(result, Exception):
            raise result
        data = result if result is not None else {
            'match': False, 'score': .4, 'reason': 'typed discovery judgement',
            'defer_reason': 'low_confidence', 'discovery': {'action': 'defer'}}
        return RuleResponse(correlation_id=request.correlation_id, status=CapabilityStatus.OK, data=data)
    value.request = semantic
    return value


async def incoming(store, message_id='m', text='请跟进接口评审'):
    return await store.ingest('qq:123:group:456', message_id, 'message.observed', {'text': text})


async def make_due(store, item, expire_fallback=False):
    detail = (await store.observation(item.id)).detail
    detail['discovery']['next_retry_at'] = '2020-01-01T00:00:00+00:00'
    if expire_fallback:
        detail['discovery']['fallback']['deadline_at'] = '2020-01-01T00:00:00+00:00'
    async with store.engine.begin() as connection:
        await connection.execute(observed_items.update().where(observed_items.c.id == item.id).values(detail=detail))


@pytest.mark.parametrize('action', ['new', 'ignore', 'defer'])
async def test_low_confidence_uses_one_fallback_even_after_restart(store, tmp_path, action):
    item = await incoming(store)
    fallback = Fallback(action)
    first = plugin(store, tmp_path, fallback)
    await first.discover(item)
    await plugin(store, tmp_path, fallback).discover(item)
    assert len(fallback.calls) == 1
    state = (await store.observation(item.id)).detail['discovery']
    assert state['status'] == {'new': 'created', 'ignore': 'ignored', 'defer': 'deferred'}[action]
    assert state['fallback']['status'] == 'completed'
    assert len(await store.channels()) == (1 if action == 'new' else 0)
    if action == 'defer':
        await make_due(store, item)
        await plugin(store, tmp_path, fallback).discover(item)
        assert len(fallback.calls) == 1


async def test_existing_fallback_uses_only_trusted_source_candidate(store, tmp_path):
    item = await incoming(store)
    owner = await store.create_channel('review', 'wait', [item.source_key])
    fallback = Fallback('existing', channel_id=owner.id)
    await plugin(store, tmp_path, fallback).discover(item)
    state = (await store.observation(item.id)).detail['discovery']
    assert state['status'] == 'existing' and state['channel_id'] == owner.id
    assert [x['id'] for x in fallback.calls[0][1]] == [owner.id]
    assert len(await store.channels()) == 1


@pytest.mark.parametrize('action', ['new', 'ignore'])
async def test_confident_result_does_not_call_fallback(store, tmp_path, action):
    item = await incoming(store)
    fallback = Fallback()
    value = plugin(store, tmp_path, fallback, result={'match': action == 'new', 'score': .9,
        'reason': 'confident', 'discovery': {'action': action}})
    await value.discover(item)
    assert fallback.calls == []
    assert (await store.observation(item.id)).detail['discovery']['status'] in {'created', 'ignored'}


async def test_plain_rule_defer_cannot_fabricate_semantic_trigger(store, tmp_path):
    item = await incoming(store)
    fallback = Fallback()
    value = plugin(store, tmp_path, fallback, engine=Engine({'action': 'defer', 'reason': 'low_confidence'}))
    await value.discover(item)
    assert not fallback.calls
    assert (await store.observation(item.id)).detail['discovery']['status'] == 'deferred'


async def test_semantic_error_does_not_call_fallback(store, tmp_path):
    item = await incoming(store)
    fallback = Fallback()
    await plugin(store, tmp_path, fallback, result=RuntimeError('provider failed')).discover(item)
    assert not fallback.calls
    assert (await store.observation(item.id)).detail['discovery']['status'] == 'error'


@pytest.mark.parametrize('error,status', [(DomainError('invalid_output'), 'failed'),
    (TimeoutError(), 'unknown'), (RuntimeError('secret must not be saved'), 'failed')])
async def test_failed_fallback_is_not_ignore_or_repeated(store, tmp_path, error, status):
    item = await incoming(store)
    fallback = Fallback(error=error)
    await plugin(store, tmp_path, fallback).discover(item)
    state = (await store.observation(item.id)).detail['discovery']
    assert state['status'] == 'error'
    assert state['fallback']['status'] == status
    assert 'secret must not be saved' not in str(state)
    await make_due(store, item)
    await plugin(store, tmp_path, fallback).discover(item)
    assert len(fallback.calls) == 1


async def test_cached_result_recovers_without_semantic_or_llm_call(store, tmp_path):
    item = await incoming(store)
    token = await store.claim_discovery_fallback(item.id, {})
    await store.finish_discovery_fallback(item.id, token, decision={'action': 'new', 'reason': 'cached'})
    fallback, engine = Fallback(), Engine()
    await plugin(store, tmp_path, fallback, engine=engine).discover(item)
    assert not fallback.calls and engine.calls == 0
    assert (await store.observation(item.id)).detail['discovery']['status'] == 'created'


async def test_expired_inflight_intent_becomes_unknown_without_reissue(store, tmp_path):
    item = await incoming(store)
    await store.claim_discovery_fallback(item.id, {})
    await make_due(store, item, expire_fallback=True)
    fallback, engine = Fallback(), Engine()
    await plugin(store, tmp_path, fallback, engine=engine).discover(item)
    assert not fallback.calls and engine.calls == 0
    state = (await store.observation(item.id)).detail['discovery']
    assert state['fallback']['status'] == 'unknown' and state['status'] == 'error'


async def test_slow_fallback_does_not_hold_discovery_lock_and_cancellation_is_durable(store, tmp_path):
    item = await incoming(store)
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []
    async def fallback(text, candidates):
        calls.append(text)
        entered.set()
        await release.wait()
        return DiscoveryDecision(action='new', reason='ready')
    value = plugin(store, tmp_path, fallback)
    task = asyncio.create_task(value.discover(item))
    await asyncio.wait_for(entered.wait(), 2)
    try:
        async with asyncio.timeout(.5):
            await value.discover(item)
            other = await incoming(store, 'other', '今天天气不错')
            value.engine = Engine({'action': 'ignore', 'reason': 'chatter'})
            await value.discover(other)
        assert (await store.observation(other.id)).detail['discovery']['status'] == 'ignored'
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert len(calls) == 1
    state = (await store.observation(item.id)).detail['discovery']
    assert state['fallback']['status'] == 'unknown'
    assert state['fallback']['error_code'] == 'cancelled'


@pytest.mark.parametrize('ordinary_action', ['ignore', 'defer'])
async def test_already_running_primary_cannot_displace_saved_fallback(store, tmp_path, ordinary_action):
    item = await incoming(store)
    entered, release = asyncio.Event(), asyncio.Event()
    class SlowEngine(Engine):
        async def run(self, source, event, context, call):
            decision = (await super().run(source, event, context, call) if ordinary_action == 'defer'
                        else {'action': 'ignore', 'reason': 'old primary answer'})
            entered.set()
            await release.wait()
            return decision
    fallback = Fallback()
    first = plugin(store, tmp_path, fallback, engine=SlowEngine())
    task = asyncio.create_task(first.discover(item))
    await asyncio.wait_for(entered.wait(), 2)
    try:
        token = await store.claim_discovery_fallback(item.id, {})
        await store.finish_discovery_fallback(item.id, token,
            decision={'action': 'new', 'reason': 'second processor completed fallback'})
    finally:
        release.set()
        await task
    state = (await store.observation(item.id)).detail['discovery']
    if ordinary_action == 'defer':
        # Losing a claim must immediately resume the persisted winner, not
        # overwrite its state and wait for a new primary classification.
        assert state['status'] == 'created'
    else:
        assert state['status'] == 'fallback_pending'
        await plugin(store, tmp_path, fallback).discover(item)
    assert (await store.observation(item.id)).detail['discovery']['status'] == 'created'
    assert len(await store.channels()) == 1
    assert not fallback.calls


@pytest.mark.parametrize('symbol', ['中', '"\n\\'])
async def test_host_multibyte_candidates_fit_same_snapshot_for_both_classifiers(store, tmp_path, symbol):
    import json
    from argus_app.infrastructure.store import channels
    from argus_app.model_provider.discovery_fallback import DiscoveryFallback
    from icarus.core.models.messages import LLMResponse, Message, TextPart
    item = await incoming(store)
    owner = await store.create_channel('评审' * 60, (symbol * 1000)[:1000], [item.source_key])
    async with store.engine.begin() as connection:
        await connection.execute(channels.update().where(channels.c.id == owner.id)
                                 .values(progress=(symbol * 1000)[:1000]))
    model_inputs = []
    class LLM:
        async def ainvoke(self, messages, tools=None):
            model_inputs.append(json.loads(messages[1].content[0].text))
            return LLMResponse(Message('assistant', [TextPart(json.dumps({
                'action': 'existing', 'channel_id': owner.id, 'reason': 'same task'}))]), finish_reason='stop')
    value = plugin(store, tmp_path, DiscoveryFallback(LLM()))
    semantic_inputs = []
    original = value.request
    async def request(call):
        semantic_inputs.append(call.args['candidates'])
        return await original(call)
    value.request = request
    await value.discover(item)
    assert len(model_inputs) == 1
    assert model_inputs[0]['candidates'] == semantic_inputs[0]
    assert len(semantic_inputs[0]) == 1 and semantic_inputs[0][0]['id'] == owner.id
    assert len(json.dumps(semantic_inputs[0][0], ensure_ascii=False, allow_nan=False).encode()) <= 4096
    state = (await store.observation(item.id)).detail['discovery']
    assert state['status'] == 'existing' and state['fallback']['status'] == 'completed'


async def test_context_changed_while_llm_waited_cannot_create_channel(store, tmp_path):
    item = await incoming(store)
    async def fallback(text, candidates):
        await store.create_channel('concurrent task', 'other goal', [item.source_key])
        return DiscoveryDecision(action='new', reason='stale')
    await plugin(store, tmp_path, fallback).discover(item)
    assert len(await store.channels()) == 1
    assert (await store.observation(item.id)).detail['discovery']['status'] == 'error'
