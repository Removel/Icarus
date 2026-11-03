"""Existing Channels may multicast; creating a Channel is only a fallback."""
import asyncio

import pytest
import pytest_asyncio

from argus_app.contracts.capabilities import RuleResponse
from argus_app.contracts.events import ObservationRecorded
from argus_app.infrastructure.store import Store
from argus_app.plugins.workflow_rules.plugin import WorkflowRules
from argus_app.rule_sdk import RuleEngine, RuleFiles
from icarus.core.capabilities import CapabilityStatus


@pytest_asyncio.fixture
async def store(database_url):
    instance = Store(database_url)
    await instance.start()
    try:
        yield instance
    finally:
        await instance.close()


async def channel(store, files, decision, *, paused=False):
    value = await store.create_channel('Existing task', 'Existing goal', ['qq:100:private:200'])
    source = 'def main(event, channel):\n    ' + decision
    artifact = files.publish(source, value.id)
    from sqlalchemy import update
    from argus_app.infrastructure.store import channels
    async with store.engine.begin() as conn:
        await conn.execute(update(channels).where(channels.c.id == value.id).values(
            **artifact, lifecycle='paused' if paused else 'active'))
    return await store.channel(value.id)


async def message(store):
    return await store.ingest('qq:100:private:200', 'request', 'message.observed',
                              {'text': 'Please help with this request'})


def workflow(store, files):
    plugin = WorkflowRules('workflow-rules', store, RuleEngine(timeout=10), files,
                           discovery_enabled=True)
    published, classifications = [], []

    async def publish(event):
        published.append(event)

    async def request(value):
        if value.args.get('mode') == 'discovery':
            classifications.append(value)
            data = {'match': True, 'reason': 'new task', 'discovery': {'action': 'new'}}
        else:
            data = {'observations': []}
        return RuleResponse(correlation_id=value.correlation_id, status=CapabilityStatus.OK, data=data)

    plugin.bind_publisher(publish)
    plugin.request = request
    return plugin, published, classifications


async def run_entry(plugin, item, entry):
    if entry == 'ingress':
        await plugin.observation_recorded('qq-protocol', ObservationRecorded(
            observation_id=item.id, source_key=item.source_key))
    elif entry == 'scan':
        await plugin.scan_once()
    else:
        await plugin.discover(item)


@pytest.mark.parametrize('entry', ['ingress', 'scan', 'discover'])
@pytest.mark.parametrize('accepts', [(True,), (True, True), (False, True)])
async def test_existing_acceptance_prevents_creation_without_exclusive_consumption(store, tmp_path, entry, accepts):
    files = RuleFiles(tmp_path)
    owners = [await channel(store, files, f'return {{"passed": {accepted}, "reason": "checked"}}')
              for accepted in accepts]
    item = await message(store)
    plugin, published, classifications = workflow(store, files)

    await run_entry(plugin, item, entry)

    assert {ch.id for ch in await store.channels()} == {ch.id for ch in owners}
    for owner, accepted in zip(owners, accepts):
        pending = (await store.channel(owner.id)).runtime['pending']
        assert (item.id in pending) is accepted
    assert {event.channel_id for event in published} == {
        owner.id for owner, accepted in zip(owners, accepts) if accepted}
    assert classifications == []
    assert (await store.observation(item.id)).detail['discovery']['status'] == 'existing'


@pytest.mark.parametrize('entry', ['ingress', 'scan', 'discover'])
async def test_new_channel_is_allowed_when_all_existing_channels_reject(store, tmp_path, entry):
    files = RuleFiles(tmp_path)
    owners = [await channel(store, files, 'return {"passed": False, "reason": "different topic"}')
              for _ in range(2)]
    item = await message(store)
    plugin, published, classifications = workflow(store, files)

    await run_entry(plugin, item, entry)

    all_channels = await store.channels()
    created = [ch for ch in all_channels if ch.id not in {owner.id for owner in owners}]
    assert len(created) == 1
    assert item.id in created[0].runtime['pending']
    assert classifications
    assert (await store.observation(item.id)).detail['discovery']['status'] == 'created'


@pytest.mark.parametrize('entry', ['ingress', 'scan', 'discover'])
@pytest.mark.parametrize('outcome', ['error', 'defer', 'paused'])
async def test_unresolved_existing_decision_does_not_mean_rejection(store, tmp_path, entry, outcome):
    files = RuleFiles(tmp_path)
    decision = ('return {"passed": bool(1 / 0), "reason": "broken"}' if outcome == 'error'
                else 'return {"passed": False, "defer": True, "reason": "need evidence"}')
    owner = await channel(store, files, decision, paused=outcome == 'paused')
    item = await message(store)
    plugin, _, classifications = workflow(store, files)

    await run_entry(plugin, item, entry)

    assert [ch.id for ch in await store.channels()] == [owner.id]
    assert classifications == []
    if outcome != 'paused':
        assert (await store.channel(owner.id)).runtime['pending'][item.id]['status'] in {'error', 'deferred'}


async def test_acceptance_survives_commit_subscription_removal_and_restart(store, tmp_path):
    files = RuleFiles(tmp_path)
    owner = await channel(store, files, 'return {"passed": True, "reason": "related"}')
    item = await message(store)
    assert await store.decide(owner.id, item.id, owner.version, {'passed': True, 'reason': 'related'})
    round_ = await store.begin_round(owner.id)
    for kind, payload in [
        ('agent.delivered', {'observation_ids': [item.id]}),
        ('agent.commit_requested', {'expected_version': owner.version,
                                   'results': [{'observation_id': item.id, 'status': 'handled'}],
                                   'lifecycle': 'completed'}),
    ]:
        control = await store.ingest('agent', kind, kind, payload,
                                     {'binding_id': round_['binding_id'], 'payload': payload})
        assert (await store.apply_control(control.id))['applied'] is True
    assert (await store.channel(owner.id)).runtime['pending'] == {}
    assert (await store.channel(owner.id)).subscriptions == {}

    restarted = Store(store.engine.url.render_as_string(hide_password=False))
    await restarted.start()
    try:
        plugin, _, classifications = workflow(restarted, files)
        await plugin.scan_once()
        assert [ch.id for ch in await restarted.channels()] == [owner.id]
        assert classifications == []
        assert await restarted.discovery_candidates() == []
    finally:
        await restarted.close()


@pytest.mark.parametrize('decision', [None, {'passed': False, 'defer': True, 'reason': 'uncertain'}, {'error': 'unavailable'}])
async def test_store_cannot_create_before_existing_rejection_is_known(store, tmp_path, decision):
    files = RuleFiles(tmp_path)
    owner = await channel(store, files, 'return {"passed": False, "reason": "unrelated"}')
    item = await message(store)
    if decision:
        assert await store.decide(owner.id, item.id, owner.version, decision)
    target = store.discovered_channel_id(item)
    artifact = files.publish('def main(event, channel):\n    return {"passed": True, "reason": "related"}', target)

    result = await store.record_discovery(item.id, {'action': 'new', 'reason': 'stale classifier'}, **artifact)

    assert result is None
    assert [ch.id for ch in await store.channels()] == [owner.id]


async def test_accepted_event_cannot_claim_fallback_or_be_created_by_stale_classifier(store, tmp_path):
    files = RuleFiles(tmp_path)
    owner = await channel(store, files, 'return {"passed": True, "reason": "related"}')
    item = await message(store)
    assert await store.decide(owner.id, item.id, owner.version, {'passed': True, 'reason': 'related'})

    assert await store.claim_discovery_fallback(item.id, {owner.id: owner.version}) is None
    artifact = files.publish('def main(event, channel):\n    return {"passed": True, "reason": "related"}',
                             store.discovered_channel_id(item))
    assert await store.record_discovery(item.id, {'action': 'new', 'reason': 'stale result'}, **artifact) is None
    assert [ch.id for ch in await store.channels()] == [owner.id]


async def test_two_workflow_instances_cannot_create_after_existing_acceptance(store, tmp_path):
    files = RuleFiles(tmp_path)
    owners = [await channel(store, files, 'return {"passed": True, "reason": "related"}') for _ in range(2)]
    item = await message(store)
    first, _, _ = workflow(store, files)
    second, _, _ = workflow(store, files)

    await asyncio.gather(first.discover(item), second.scan_once())

    assert {ch.id for ch in await store.channels()} == {ch.id for ch in owners}
    for owner in owners:
        assert item.id in (await store.channel(owner.id)).runtime['pending']


@pytest.mark.parametrize('late_result', ['primary', 'fallback'])
async def test_late_new_decision_cannot_override_acceptance(store, tmp_path, late_result):
    from argus_app.contracts.models import DiscoveryDecision

    files = RuleFiles(tmp_path)
    item = await message(store)
    plugin, _, _ = workflow(store, files)
    entered, release = asyncio.Event(), asyncio.Event()
    original = plugin.request

    async def request(value):
        if value.args.get('mode') != 'discovery':
            return await original(value)
        if late_result == 'primary':
            entered.set()
            await release.wait()
            return await original(value)
        return RuleResponse(correlation_id=value.correlation_id, status=CapabilityStatus.OK,
                            data={'match': False, 'reason': 'uncertain', 'defer_reason': 'low_confidence',
                                  'discovery': {'action': 'defer'}})

    async def fallback(**kwargs):
        entered.set()
        await release.wait()
        return DiscoveryDecision(action='new', reason='late new-topic answer')

    plugin.request = request
    plugin.discovery_fallback = fallback
    task = asyncio.create_task(plugin.discover(item))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        owner = await channel(store, files, 'return {"passed": True, "reason": "accepted while classifier waited"}')
        await plugin.process_channel(owner.id)
    finally:
        release.set()
        await task

    assert [ch.id for ch in await store.channels()] == [owner.id]
    assert item.id in (await store.channel(owner.id)).runtime['pending']
    assert (await store.observation(item.id)).detail['discovery']['status'] == 'existing'


async def test_blocked_discovery_batch_does_not_starve_later_unrelated_event(store, tmp_path):
    files = RuleFiles(tmp_path)
    owner = await channel(store, files, 'return {"passed": False, "reason": "unrelated"}', paused=True)
    blocked = [await store.ingest('qq:100:private:200', f'blocked-{i}', 'message.observed',
                                 {'text': f'Waiting request {i}'}) for i in range(20)]
    ready = await store.ingest('qq:100:private:300', 'unrelated', 'message.observed',
                               {'text': 'A different request with no existing subscribers'})
    plugin, _, _ = workflow(store, files)

    # Only DB recovery runs: no ingress notification can rescue the later item.
    await plugin.scan_once()
    await plugin.scan_once()

    channels = await store.channels()
    assert len(channels) == 2
    assert (await store.observation(ready.id)).detail['discovery']['status'] == 'created'
    assert (await store.channel(owner.id)).runtime['pending'] == {}
    for item in blocked:
        assert (await store.observation(item.id)).detail.get('discovery', {}).get('status') != 'created'


async def test_discovery_recovery_wraps_back_to_an_earlier_due_retry(store, tmp_path):
    from argus_app.infrastructure.store import observed_items

    files = RuleFiles(tmp_path)
    earlier = await message(store)
    await store.ingest('qq:100:private:300', 'later', 'message.observed', {'text': 'Later unrelated task'})
    plugin, _, _ = workflow(store, files)
    original = plugin.request
    retry_ready = False

    async def request(value):
        if value.args.get('mode') == 'discovery' and value.args.get('text') == earlier.content['text'] and not retry_ready:
            return RuleResponse(correlation_id=value.correlation_id, status=CapabilityStatus.OK,
                                data={'match': False, 'reason': 'not enough evidence', 'discovery': {'action': 'defer'}})
        return await original(value)

    plugin.request = request
    await plugin.scan_once()
    detail = (await store.observation(earlier.id)).detail
    assert detail['discovery']['status'] == 'deferred'
    detail['discovery']['next_retry_at'] = '2020-01-01T00:00:00+00:00'
    async with store.engine.begin() as conn:
        await conn.execute(observed_items.update().where(observed_items.c.id == earlier.id).values(detail=detail))
    retry_ready = True

    await plugin.scan_once()

    assert (await store.observation(earlier.id)).detail['discovery']['status'] == 'created'
    assert len(await store.channels()) == 2
