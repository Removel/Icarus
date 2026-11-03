"""New topics must not be hidden by a source subscription."""
from uuid import uuid4
import pytest

from apps.argus.test.argus_app.infrastructure.test_store import store


async def test_existing_subscription_does_not_hide_new_topic(store):
    existing = await store.create_channel('A', 'approval', ['qq:1'])
    item = await store.ingest('qq:1', 'new-topic', 'message.observed', {'text': '请帮我跟进体检预约'})
    assert await store.decide(existing.id, item.id, existing.version,
                              {'passed': False, 'reason': 'not an approval topic'})
    new_id = store.discovered_channel_id(item)
    result = await store.record_discovery(item.id, {'action': 'new', 'reason': 'new health task'},
        rule_path=f'{new_id}/' + 'a' * 64 + '.py', rule_hash='a' * 64)
    assert result and result != existing.id
    assert len(await store.channels()) == 2
    assert await store.record_discovery(item.id, {'action': 'new', 'reason': 'replay'}) == result


async def test_same_topic_concurrent_events_reuse_channel(store):
    ids = []
    for index in range(2):
        item = await store.ingest('qq:1', str(index), 'message.observed', {'text': '请帮我跟进同一审批'})
        target = store.discovered_channel_id(item)
        ids.append(await store.record_discovery(item.id, {'action': 'new', 'reason': 'request'},
                    rule_path=f'{target}/' + 'a' * 64 + '.py', rule_hash='a' * 64))
    assert ids[0] == ids[1]
    assert len(await store.channels()) == 1


async def test_existing_route_must_be_authorized_and_does_not_seed_twice(store):
    channel = await store.create_channel('A', 'approval', ['qq:1'])
    item = await store.ingest('qq:1', 'followup', 'message.observed', {'text': '审批进展'})
    result = await store.record_discovery(item.id, {'action': 'existing', 'channel_id': channel.id, 'reason': 'followup'})
    assert result is None  # routing does not bypass the normal Channel rule
    assert (await store.observation(item.id)).detail['discovery']['channel_id'] == channel.id
    assert (await store.channel(channel.id)).runtime['pending'] == {}
