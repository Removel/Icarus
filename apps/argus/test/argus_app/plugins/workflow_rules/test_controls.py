from types import SimpleNamespace
from uuid import uuid4

import pytest

from argus_app.contracts.models import Channel, Observation
from argus_app.contracts.capabilities import RuleControlRequest
from argus_app.plugins.workflow_rules.plugin import WorkflowRules
from argus_app.rule_sdk.engine import RuleEngine
from argus_app.rule_sdk.files import RuleFiles


class ControlStore:
    def __init__(self):
        self.channel_value = Channel(id=str(uuid4()), title='test', goal='goal', subscriptions={'qq:group:1': None})
        self.rows = {}
        self.completed = []
        self.applied = []
    async def bound_channel(self, binding):
        return self.channel_value
    async def observation(self, id):
        return self.rows[id]
    async def observations(self, **kwargs):
        return []
    async def complete_control(self, id, result):
        self.completed.append(result)
        return result
    async def apply_control(self, id):
        self.applied.append(id)
        return {'applied': True}
    def control(self, kind, payload):
        value = Observation(id=str(uuid4()), source_key='agent:binding', message_id='req', kind=kind, revision=1, observed_at='2026-11-01T00:00:00Z', content=payload, detail={'binding_id': 'binding', 'payload': payload})
        self.rows[value.id] = value
        return value


@pytest.mark.asyncio
async def test_check_runs_rule_before_immutable_publish(tmp_path):
    store = ControlStore()
    files = RuleFiles(tmp_path)
    plugin = WorkflowRules('workflow-rules', store, RuleEngine(), files)
    control = store.control('rule.check_requested', {'source': 'def main(event, channel):\n    return {"passed": False, "reason": "ignore"}'})
    response = (await plugin.control_requested('agent-protocol', RuleControlRequest(channel_id=store.channel_value.id, args={'observation_id': control.id})))[0]
    assert response.unwrap().data['applied'] is True
    assert response.data['passed'] is True
    assert list(tmp_path.rglob('*.py')) == []
    assert store.applied == []


@pytest.mark.asyncio
async def test_failed_runtime_check_does_not_publish(tmp_path):
    store = ControlStore()
    plugin = WorkflowRules('workflow-rules', store, RuleEngine(), RuleFiles(tmp_path))
    control = store.control('rule.check_requested', {'source': 'def main(event, channel):\n    return {"passed": bool(1 / 0), "reason": "bad"}'})
    response = (await plugin.control_requested('agent-protocol', RuleControlRequest(channel_id=store.channel_value.id, args={'observation_id': control.id})))[0]
    assert response.unwrap().data['applied'] is True
    assert response.data['passed'] is True
    assert list(tmp_path.rglob('*.py')) == []


@pytest.mark.asyncio
async def test_invalid_hash_update_persists_rejected_control_without_store_apply(tmp_path):
    store = ControlStore()
    plugin = WorkflowRules('workflow-rules', store, RuleEngine(), RuleFiles(tmp_path))
    control = store.control('channel.update_requested', {'expected_version': 1, 'rule_hash': 'bad', 'rule_path': f'{store.channel_value.id}/bad.py'})
    response = (await plugin.control_requested('agent-protocol', RuleControlRequest(channel_id=store.channel_value.id, args={'observation_id': control.id})))[0]
    assert response.unwrap().data['applied'] is False
    assert store.applied == []
    assert store.completed[0]['code'] == 'legacy_control_disabled'


@pytest.mark.asyncio
async def test_rule_cannot_override_captured_source_bounds(tmp_path):
    store = ControlStore()
    plugin = WorkflowRules('workflow-rules', store, RuleEngine(), RuleFiles(tmp_path))
    class Requester:
        async def request(self, event):
            from argus_app.contracts.capabilities import RuleResponse
            from icarus.core.capabilities import CapabilityStatus
            assert event.args['source_bounds'] == {'qq:group:1': 'frozen-id'}
            return RuleResponse(correlation_id=event.correlation_id, status=CapabilityStatus.OK, data={'observations': []})
    plugin.bind_requester(Requester(), None)
    call = plugin._broker(store.channel_value, {'qq:group:1': 'frozen-id'})
    assert await call('history', {'source_bounds': {'secret': 'unbounded'}}) == {'observations': []}


@pytest.mark.asyncio
async def test_concurrent_check_replay_does_not_rerun_capabilities(tmp_path):
    import asyncio
    store = ControlStore()
    class CountingEngine(RuleEngine):
        calls = 0
        async def evaluate(self, *args):
            self.calls += 1
            await asyncio.sleep(0.01)
            return await super().evaluate(*args)
    original_complete = store.complete_control
    async def complete(id, result):
        store.rows[id] = store.rows[id].model_copy(update={'detail': {**store.rows[id].detail, 'result': result}})
        return await original_complete(id, result)
    store.complete_control = complete
    engine = CountingEngine()
    plugin = WorkflowRules('workflow-rules', store, engine, RuleFiles(tmp_path))
    control = store.control('rule.check_requested', {'source': 'def main(event, channel):\n    return {"passed": False, "reason": "ignore"}'})
    first, second = await asyncio.gather(plugin._control(control), plugin._control(control))
    assert first == second
    assert engine.calls == 0
