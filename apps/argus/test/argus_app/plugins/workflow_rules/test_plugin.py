from uuid import uuid4
import pytest
from argus_app.contracts.models import Channel, Observation, Decision
from argus_app.plugins.workflow_rules.plugin import WorkflowRules
from argus_app.rule_sdk.engine import RuleEngine
from argus_app.rule_sdk.files import RuleFiles


class StoreFake:
    def __init__(self, channel, event):
        self.ch = channel
        self.event = event
        self.decisions = []
    async def channel(self, id):
        return self.ch
    async def channels(self):
        return [self.ch]
    async def unread(self, id, limit=50):
        return [self.event]
    async def observations(self, source_keys, limit=20, **kwargs):
        return [self.event]
    async def decide(self, channel_id, observation_id, expected_version, decision):
        assert expected_version == self.ch.version
        self.decisions.append(decision)
        return True
    async def pending_controls(self):
        return []


def facts():
    event = Observation(id=str(uuid4()), source_key='qq:group:1', message_id='m', kind='message.observed', revision=1, observed_at='2026-11-01T00:00:00Z', content={'text': 'hello'})
    channel = Channel(id=str(uuid4()), title='title', goal='goal', subscriptions={event.source_key: None})
    return channel, event


@pytest.mark.asyncio
async def test_workflow_records_pass_before_notification(tmp_path):
    ch, event = facts()
    files = RuleFiles(tmp_path)
    ch = ch.model_copy(update=files.publish('def decide(event, channel):\n    return {"passed": True, "reason": "matched"}', ch.id))
    store = StoreFake(ch, event)
    plugin = WorkflowRules('workflow-rules', store, RuleEngine(), files)
    events = []
    async def publish(event):
        assert store.decisions[-1]['passed'] is True
        events.append(event)
    plugin.bind_publisher(publish)
    await plugin.process_channel(ch.id)
    assert len(events) == 1
    assert store.decisions == [{'passed': True, 'reason': 'matched', 'evidence_refs': [], 'defer': False}]


@pytest.mark.asyncio
async def test_failed_rule_is_persisted_as_error_not_false(tmp_path):
    ch, event = facts()
    files = RuleFiles(tmp_path)
    ch = ch.model_copy(update=files.publish('def decide(event, channel):\n    return {"passed": bool(1 / 0), "reason": "bad"}', ch.id))
    store = StoreFake(ch, event)
    plugin = WorkflowRules('workflow-rules', store, RuleEngine(), files)
    await plugin.process_channel(ch.id)
    assert store.decisions[0]['error']
    assert len(store.decisions[0]['error']) <= 512
    assert 'passed' not in store.decisions[0]


@pytest.mark.asyncio
async def test_paused_channel_is_not_evaluated(tmp_path):
    ch, event = facts()
    ch = ch.model_copy(update={'lifecycle': 'paused'})
    store = StoreFake(ch, event)
    plugin = WorkflowRules('workflow-rules', store, RuleEngine(), RuleFiles(tmp_path))
    await plugin.process_channel(ch.id)
    assert store.decisions == []


@pytest.mark.asyncio
async def test_pending_capacity_does_not_block_later_channel_scan(tmp_path):
    from argus_app.contracts.models import DomainError
    first, event = facts()
    second = first.model_copy(update={'id': str(uuid4())})
    class Store(StoreFake):
        async def channels(self):
            return [first, second]
        async def channel(self, id):
            return first if id == first.id else second
        async def decide(self, channel_id, observation_id, expected_version, decision):
            if channel_id == first.id:
                raise DomainError('pending_capacity')
            self.decisions.append(channel_id)
            return True
    store = Store(first, event)
    plugin = WorkflowRules('workflow-rules', store, RuleEngine(), RuleFiles(tmp_path))
    await plugin.scan_once()
    assert store.decisions == [second.id]
