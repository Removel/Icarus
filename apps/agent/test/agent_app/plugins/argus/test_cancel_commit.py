import asyncio
from types import SimpleNamespace

import httpx
import pytest

from apps.agent.test.agent_app.plugins.argus.test_commit_tools import fixture
from agent_app.plugins.argus.tools import ArgusTool


@pytest.mark.parametrize('cancel_at', ['query', 'append'])
def test_cancellation_during_reconciliation_or_outbox_never_posts(monkeypatch, cancel_at):
    plugin, binding, records, requests = fixture(monkeypatch)
    tool = ArgusTool(plugin, 'argus_commit')
    body = {'binding_id': 'binding', 'request_id': 'same', 'kind': 'agent.commit_requested',
            'payload': {'expected_version': 3, 'results': []}}
    if cancel_at == 'query':
        records.append(SimpleNamespace(type='argus.callback', payload={'body': body}, task_id='task'))
    original_append = plugin.bridge.append
    async def append(*args):
        await original_append(*args)
        if cancel_at == 'append':
            plugin.task_channels.request_cancel('task', 'cancel during persistence')
    plugin.bridge.append = append
    class Client:
        async def get(self, url):
            requests.append('GET')
            plugin.task_channels.request_cancel('task', 'cancel during status query')
            return httpx.Response(200, request=httpx.Request('GET', url), json={'state': 'not_found'})
        async def post(self, url, **kwargs):
            requests.append('POST')
            return httpx.Response(200, request=httpx.Request('POST', url), json={'applied': True, 'committed': True})
    async def run():
        from icarus.core.run_control.channel import AgentRunCancelled
        with pytest.raises(AgentRunCancelled):
            await tool._callback(Client(), 'task', binding, body['kind'], body['payload'], 'same')
        assert 'POST' not in requests
    asyncio.run(run())
