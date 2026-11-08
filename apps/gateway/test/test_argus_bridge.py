"""Argus protected bridge uses the production runtime and offline model fixture."""
import asyncio
import json
from uuid import uuid4

import httpx
from agent_app.application import AgentRuntime
from apps.gateway.src.app import create_app
from apps.gateway.test.test_runtime_e2e import config_for, local_provider, ProviderHandler


def test_argus_real_runtime_retry_restart_and_auth(tmp_path, monkeypatch):
    monkeypatch.setenv('ARGUS_GATEWAY_TOKEN', 'gateway-secret')
    monkeypatch.setenv('ARGUS_AGENT_API_TOKEN', 'agent-secret')
    monkeypatch.setenv('ARGUS_WORKSPACE_PATH', str(tmp_path / 'workspace'))
    (tmp_path / 'workspace').mkdir()
    channel, wake, binding, item = [str(uuid4()) for _ in range(4)]
    async def run(config, restart=False):
        runtime = AgentRuntime(config_loader=lambda: config)
        app = create_app(runtime)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://gateway') as client:
                assert (await client.post('/argus/session', json={'channel_id':channel,'request_id':'ensure','session_key':'slot-a'})).status_code == 401
                client.headers['Authorization'] = 'Bearer gateway-secret'
                response = await client.post('/argus/session', json={'channel_id':channel,'request_id':'ensure','session_key':'slot-a'})
                assert response.status_code == 200, response.text
                session = response.json()['session_id']
                other = await client.post('/argus/session', json={'channel_id':channel,
                    'request_id':'second-selection', 'session_key':'slot-b'})
                assert other.status_code == 200
                assert other.json()['session_id'] != session
                missing_key = await client.post('/argus/session', json={'channel_id':channel,'request_id':'bad'})
                assert missing_key.status_code == 422
                if not restart:
                    for operation in (
                        runtime.create_session(tmp_path / 'workspace', session),
                        runtime.submit(tmp_path / 'workspace', session, 'injected', submission_id='public'),
                        runtime.steer_task(tmp_path / 'workspace', session, 'unknown', 'injected'),
                    ):
                        try:
                            await operation
                            assert False, 'Public operation accepted a reserved bridge session'
                        except ValueError:
                            pass
                malformed = await client.post('/argus/session', json={'channel_id':channel,'request_id':'ensure','session_key':'slot-a','extra':True})
                assert malformed.status_code == 422
                payload = dict(channel_id=channel,wake_id=wake,binding_id=binding,channel_version=1,session_id=session,session_key='slot-a',
                    channel_snapshot=dict(id=channel,title='task',goal='goal',progress='',lifecycle='active',version=1,source_keys=['qq:a'],rule_source=''), rule_sdk={'version':2},
                    items=[dict(id=item,source_key='qq:a',message_id='1',kind='message.observed',revision=1,
                                observed_at='2026-11-01T00:00:00Z',content={'text':'Argus evidence'},detail={})],
                    argus_url='http://127.0.0.1:1')
                response = await client.post('/argus/wake', json=payload)
                assert response.status_code == 200, response.text
                task_id = response.json()['task_id']
                assert response.json()['admission_state'] == 'accepted'
                assert response.json()['attempt'] == 1
                assert response.json()['retryable'] is False
                assert (await client.post('/argus/wake', json=payload)).json()['task_id'] == task_id
                assert (await client.post('/argus/wake', json=payload | {'channel_version':2})).status_code == 422
                for _ in range(200):
                    status = (await client.post('/argus/status', json={'wake_id':wake})).json()
                    if status['status'] in {'completed','failed'}:
                        break
                    await asyncio.sleep(.02)
                assert status == {'task_id':task_id,'session_id':session,'status':'completed','commit_state':'none','replay_safe':False,
                    'admission_state':'accepted','attempt':1,'retryable':False,'error_code':None}
                before_notify_calls=len([p for path,p in ProviderHandler.requests if path.endswith('/chat/completions')])
                notice={'wake_id':wake,'session_id':session,'binding_id':binding,'watermark':'new-information'}
                assert (await client.post('/argus/notify',json=notice,headers={'Authorization':'Bearer wrong'})).status_code==401
                assert (await client.post('/argus/notify',json=notice|{'body':'not allowed'})).status_code==422
                assert (await client.post('/argus/notify',json=notice|{'session_id':'wrong'})).status_code==409
                assert (await client.post('/argus/notify',json=notice)).json()=={'notified':False}
                assert len([p for path,p in ProviderHandler.requests if path.endswith('/chat/completions')])==before_notify_calls
                unsafe = await client.post('/argus/wake', json=payload | {'wake_id': str(uuid4()),
                    'binding_id': str(uuid4()), 'session_id': other.json()['session_id'], 'session_key': 'slot-b'})
                assert unsafe.status_code == 409, 'new Session must not replay an unresolved Channel execution'
                return task_id
    with local_provider() as port:
        config = config_for(tmp_path / 'data', port)
        first = asyncio.run(run(config))
        assert asyncio.run(run(config, True)) == first
    assert len([p for path,p in ProviderHandler.requests if path.endswith('/chat/completions')]) == 1
