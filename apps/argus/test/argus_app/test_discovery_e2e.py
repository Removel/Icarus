"""Empty-source bootstrap over real HTTP/PG/Agent, with deterministic model only."""
import asyncio
import hashlib
import hmac
import json

import httpx
import pytest
from pydantic import SecretStr

from agent_app.application import AgentRuntime
from apps.gateway.src.app import create_app as gateway_app
from apps.gateway.test.test_argus_tools_e2e import ArgusProvider
from apps.gateway.test.test_runtime_e2e import config_for, local_provider
from argus_app.application import create_app
from argus_app.config import ArgusConfig
from argus_app.infrastructure.store import Store
from apps.argus.test.argus_app.test_closed_loop import listener, serve


@pytest.mark.asyncio
async def test_empty_source_discovers_and_delivers_first_event_without_manual_channel(tmp_path, monkeypatch, database_url):
    monkeypatch.setenv('ARGUS_GATEWAY_TOKEN', 'gateway-secret')
    monkeypatch.setenv('ARGUS_AGENT_API_TOKEN', 'callback-secret')
    monkeypatch.setenv('ARGUS_WORKSPACE_PATH', str(tmp_path / 'workspace'))
    gs, ars = listener(), listener()
    gateway_url = f'http://127.0.0.1:{gs.getsockname()[1]}'
    argus_url = f'http://127.0.0.1:{ars.getsockname()[1]}'
    store = Store(database_url)
    config = ArgusConfig(database_url=SecretStr(database_url), data_dir=tmp_path / 'argus',
        agent_token=SecretStr('callback-secret'), gateway_token=SecretStr('gateway-secret'),
        gateway_url=gateway_url, public_url=argus_url, qq_token=SecretStr('qq-secret'),
        discovery_enabled=True, scan_interval=.1)
    app = create_app(config, store=store)
    # Provider's model responses are deterministic; callback requests go to real Argus.
    ArgusProvider.callbacks, ArgusProvider.model_calls = [], []
    with local_provider(ArgusProvider) as port:
        runtime = AgentRuntime(config_loader=lambda: config_for(tmp_path / 'agent', port))
        gateway = gateway_app(runtime)
        async with serve(gateway, gs), serve(app, ars):
            async with httpx.AsyncClient(base_url=argus_url, timeout=10) as client:
                headers = {'Authorization': 'Bearer callback-secret'}
                assert (await client.get('/channels', headers=headers)).json() == []
                payload = {'post_type': 'message', 'self_id': 123, 'message_type': 'group', 'group_id': 456,
                           'user_id': 789, 'message_id': 'first-request', 'raw_message': '请帮我记录明天上午十点的体检安排'}
                raw = json.dumps(payload).encode()
                signature = 'sha1=' + hmac.new(b'qq-secret', raw, hashlib.sha1).hexdigest()
                response = await client.post('/protocols/qq', content=raw, headers={'X-Signature': signature})
                assert response.status_code == 200, response.text
                first_id = response.json()['observation_id']
                async with asyncio.timeout(25):
                    while True:
                        channels = (await client.get('/channels', headers=headers)).json()
                        if len(channels) == 1 and channels[0]['runtime']['round'] is None and not channels[0]['runtime']['pending']:
                            break
                        await asyncio.sleep(.03)
                ch = channels[0]
                assert ch['goal'] == payload['raw_message']
                assert ch['rule_path'] and len(ch['rule_hash']) == 64
                item = await store.observation(first_id)
                assert item.detail['discovery'] == {'status': 'created', 'channel_id': ch['id']}
                # Same signed event delivered again cannot create another Channel/round.
                again = await client.post('/protocols/qq', content=raw, headers={'X-Signature': signature})
                assert again.json()['observation_id'] == first_id
                await app.state.host.plugin_manager.event_bus.drain()
                assert len(await store.channels()) == 1
                assert (await store.channel(ch['id'])).runtime['round'] is None
                assert len(ArgusProvider.model_calls) <= 4
