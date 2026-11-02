"""Initial discovery shares the rule engine, but never fabricates a Channel identity."""
from uuid import uuid4

import pytest

from argus_app.contracts.models import Observation
from argus_app.plugins.workflow_rules.plugin import WorkflowRules
from argus_app.rule_sdk import RuleEngine, RuleFiles


class EmptyStore:
    def __init__(self, event):
        self.event = event
        self.recorded = []
    async def channels(self):
        return []
    async def observation(self, id):
        return self.event
    async def discovery_candidates(self, limit=20):
        return [self.event]
    def discovered_channel_id(self, item):
        return str(uuid4())
    async def record_discovery(self, observation_id, decision, **artifact):
        self.recorded.append((observation_id, decision, artifact))
        return 'new-channel' if decision.get('action') == 'new' else None


def event(text):
    return Observation(id=str(uuid4()), source_key='qq:123:group:456', message_id='m',
                       kind='message.observed', revision=1, observed_at='2026-11-02T00:00:00Z', content={'text': text})


@pytest.mark.asyncio
async def test_unsubscribed_request_is_discovered_without_system_channel(tmp_path):
    item = event('请帮我跟进一下明天的会议安排')
    store = EmptyStore(item)
    plugin = WorkflowRules('workflow-rules', store, RuleEngine(), RuleFiles(tmp_path))
    assert callable(getattr(plugin, 'discover', None)), 'Rules need an initial discovery path before a Channel exists'
    published = []
    async def publish(value):
        published.append(value)
    plugin.bind_publisher(publish)
    from argus_app.contracts.capabilities import RuleResponse
    from icarus.core.capabilities import CapabilityStatus
    async def classify(request):
        return RuleResponse(correlation_id=request.correlation_id, status=CapabilityStatus.OK,
                            data={'match': True, 'reason': 'request', 'discovery': {'action': 'new'}})
    plugin.request = classify
    await plugin.discover(item)
    assert store.recorded[0][1]['action'] == 'new'
    assert store.recorded[0][2]['rule_path']
    assert published[0].channel_id == 'new-channel'


@pytest.mark.asyncio
async def test_initial_default_rule_ignores_chatter(tmp_path):
    item = event('今天天气不错')
    store = EmptyStore(item)
    plugin = WorkflowRules('workflow-rules', store, RuleEngine(), RuleFiles(tmp_path))
    assert callable(getattr(plugin, 'discover', None))
    from argus_app.contracts.capabilities import RuleResponse
    from icarus.core.capabilities import CapabilityStatus
    async def classify(request):
        return RuleResponse(correlation_id=request.correlation_id, status=CapabilityStatus.OK,
                            data={'match': False, 'reason': 'chatter', 'discovery': {'action': 'ignore'}})
    plugin.request = classify
    await plugin.discover(item)
    assert store.recorded[0][1]['action'] == 'ignore'


@pytest.mark.asyncio
async def test_discovery_history_is_source_scoped_without_persistent_channel(tmp_path):
    item = event('请帮我安排一下')
    store = EmptyStore(item)
    class Engine:
        async def run(self, source, incoming, context, call):
            assert context['mode'] == 'discovery' and 'id' not in context
            result = await call('history', {'limit': 5})
            assert result['observations'][0]['id'] == item.id
            with pytest.raises(ValueError, match='source'):
                await call('history', {'source_keys': ['other:tenant']})
            return {'action': 'ignore', 'reason': 'checked history'}
    async def observations(source_keys, **kwargs):
        assert source_keys == [item.source_key]
        return [item]
    store.observations = observations
    plugin = WorkflowRules('workflow-rules', store, Engine(), RuleFiles(tmp_path))
    from argus_app.plugins.rule_capabilities.observation_query.plugin import ObservationQueryPlugin
    provider = ObservationQueryPlugin('observation-query', store)
    async def request(value):
        return (await provider.query('workflow-rules', value))[0]
    plugin.request = request
    assert callable(getattr(plugin, 'discover', None))
    await plugin.discover(item)
    assert store.recorded[0][1]['action'] == 'ignore'
