import asyncio
from types import SimpleNamespace

import httpx
import pytest

from agent_app.plugins.argus.tools import ArgusTool, MODELS, TOOL_NAMES
from icarus.core.models.messages import Message
from icarus.core.run_control import TaskChannelRegistry


def test_commit_schema_only_requests_business_fields():
    assert 'argus_commit' in MODELS
    assert set(MODELS['argus_commit'].model_json_schema()['properties']) == {
        'results', 'progress', 'source_refs', 'rule_source', 'lifecycle', 'next_session'}
    assert set(TOOL_NAMES) == {'argus_channel_read', 'argus_read', 'argus_history', 'argus_rule_check', 'argus_commit', 'argus_guide', 'argus_receive'}
    assert set(MODELS['argus_rule_check'].model_json_schema()['properties']) == {'source'}


def fixture(monkeypatch):
    monkeypatch.setenv('ARGUS_AGENT_API_TOKEN', 'synthetic-secret')
    records, requests = [], []
    wake = {'channel_id': 'channel', 'binding_id': 'binding', 'channel_version': 3,
            'argus_url': 'http://argus', 'items': [{'id': 'observation'}]}
    binding = {'wake': wake, 'prompt': 'COMPLETE CURRENT EVIDENCE', 'delivered': True, 'finished': False}
    class Store:
        async def read_updates(self, identity):
            return tuple(records), len(records)
    async def append(identity, kind, payload, task_id):
        records.append(SimpleNamespace(type=kind, payload=payload, task_id=task_id))
    bridge = SimpleNamespace(bindings={'task': binding}, callback_locks={},
                             runtime=SimpleNamespace(session_store=Store()),
                             append=append, identity_for_wake=lambda wake: 'bound-session')
    channels = TaskChannelRegistry()
    channels.create('task')
    plugin = SimpleNamespace(bridge=bridge, task_channels=channels)
    original = httpx.AsyncClient
    def respond(request):
        import json
        requests.append(request)
        if request.method == 'POST':
            value = json.loads(request.content)
            assert value['kind'] == 'agent.commit_requested'
            assert value['payload']['expected_version'] == 3
            assert value['payload']['results'] == [{'observation_id': 'observation', 'status': 'handled'}]
            return httpx.Response(200, json={'applied': True, 'committed': True, 'version': 4})
        return httpx.Response(200, json={'state': 'not_found'})
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: original(transport=httpx.MockTransport(respond), **kwargs))
    return plugin, binding, records, requests


def test_commit_auto_binds_ids_and_version_and_replays_receipt(monkeypatch):
    plugin, binding, records, requests = fixture(monkeypatch)
    tool = ArgusTool(plugin, 'argus_commit')
    args = {'results': [{'ref': '1', 'status': 'handled'}], 'progress': 'done'}
    async def run():
        first = await tool.ainvoke(args, task_id='task')
        assert first.success, first.error
        second = await tool.ainvoke(args, task_id='task')
        assert second.success and second.output == first.output
        assert len([r for r in requests if r.method == 'POST']) == 1
        assert binding['finished'] is True
    asyncio.run(run())


def test_commit_uses_bound_session_not_latest_channel_identity(monkeypatch):
    plugin, binding, records, requests = fixture(monkeypatch)
    binding['wake'].update(session_id='session-a', session_key='slot-a')
    calls = []
    def old_identity(channel):
        raise AssertionError('current Channel Session must not choose old callback journal')
    plugin.bridge.identity = old_identity
    plugin.bridge.identity_for_wake = lambda wake: calls.append(wake['session_id']) or 'session-a'
    tool = ArgusTool(plugin, 'argus_commit')
    async def run():
        result = await tool.ainvoke({'results': [{'ref': '1', 'status': 'handled'}],
            'progress': 'next stage', 'next_session': 'new'}, task_id='task')
        assert result.success, result.error
        assert calls == ['session-a']
    asyncio.run(run())


def test_commit_requires_complete_round_and_visible_evidence(monkeypatch):
    plugin, binding, records, requests = fixture(monkeypatch)
    tool = ArgusTool(plugin, 'argus_commit')
    async def run():
        bad = await tool.ainvoke({'results': [{'ref': 'old', 'status': 'handled'}]}, task_id='task')
        assert not bad.success
        binding['delivered'] = False
        hidden = await tool.ainvoke({'results': [{'ref': '1', 'status': 'handled'}]}, task_id='task')
        assert not hidden.success
        assert not requests
    asyncio.run(run())
