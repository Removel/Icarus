"""Stopped tasks only query previously persisted commit intents, never POST them."""
import asyncio
from types import SimpleNamespace

import httpx
import pytest

from agent_app.application.services.argus_bridge import ArgusBridge


def test_stopped_execution_without_commit_is_not_replay_safe():
    bridge = ArgusBridge(SimpleNamespace())
    claim = SimpleNamespace(task_id='task', type='argus.wake', payload={'binding': {'wake': {}}})
    records = [claim, SimpleNamespace(task_id='task', type='task.finished', payload={'status': 'completed'})]
    async def run():
        result = await bridge._status((SimpleNamespace(session_id='bound-session'), claim, records))
        assert result['status'] == 'completed'
        assert result['commit_state'] == 'none'
        assert result['replay_safe'] is False
    asyncio.run(run())


@pytest.mark.parametrize('state', ['applied', 'pending', 'not_found', 'rejected'])
def test_stopped_task_commit_reconciliation_is_read_only(monkeypatch, state):
    monkeypatch.setenv('ARGUS_AGENT_API_TOKEN', 'synthetic-token')
    bridge = ArgusBridge(SimpleNamespace())
    identity, requests, written = 'identity', [], []
    body = {'binding_id': 'binding', 'request_id': 'request', 'kind': 'agent.commit_requested', 'payload': {}}
    records = [SimpleNamespace(task_id='task', type='argus.wake', payload={'binding': {'wake': {'argus_url': 'http://argus'}}}),
               SimpleNamespace(task_id='task', type='argus.callback', payload={'body': body})]
    async def append(identity, kind, payload, task_id):
        written.append((kind, payload))
    bridge.append = append
    original = httpx.AsyncClient
    def respond(request):
        requests.append(request)
        assert request.method == 'GET'
        return httpx.Response(200, json={'state': state, 'result': {'applied': state == 'applied', 'committed': state == 'applied'}})
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: original(transport=httpx.MockTransport(respond), **kwargs))
    async def run():
        assert hasattr(bridge, '_reconcile_commits'), 'stopped tasks need receipt-only recovery'
        result = await bridge._reconcile_commits(identity, 'task', records)
        assert result == ('unknown' if state == 'not_found' else state)
        assert len(requests) == 1
        assert bool(written) is (state in {'applied', 'rejected'})
    asyncio.run(run())
