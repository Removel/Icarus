"""Signed group flow is tested with a closed batch, not one Jev call per message."""
import asyncio
import hashlib
import hmac
import json
from typesafe_sdk import SystemOneResponse,Usage,ChoiceAnswer
from fastapi.testclient import TestClient
from apps.argus.src.config.config_model import ArgusConfig,QQConfig
from apps.argus.src.dao.observation_store import ObservationStore
from apps.argus.src.main import create_app
from apps.argus.src.clients.icarus_client import GatewayBusinessError


def test_three_group_messages_close_as_one_decision(tmp_path):
    class Judge:
        calls=0
        async def ask(self,**kwargs):
            self.calls+=1
            return SystemOneResponse(model='fake',usage=Usage(),answers={'intervene':ChoiceAnswer(choice='yes',confidence=1.,probabilities={'yes':1.})})
    class Gateway:
        connected=True
        def __init__(self):self.sessions={};self.prompts=[]
        async def connect(self):pass
        async def close(self):pass
        async def session_get(self,path,sid):
            if sid not in self.sessions:raise GatewayBusinessError('session_not_found','not found')
            return self.sessions[sid]
        async def session_create(self,path,sid):
            self.sessions[sid]=dict(workspace_key='key',session_id=sid,lifecycle='ready',active_task_ids=[])
            return self.sessions[sid]
        async def session_submit(self,**kwargs):self.prompts.append(kwargs['prompt']);return {'task_id':'t'}
    config=ArgusConfig(host='127.0.0.1',port=8766,log_level='INFO',queue_capacity=10,worker_count=1,
        qq=QQConfig(enabled=True,bot_id='10000',workspace_path=str(tmp_path),gateway_url='ws://127.0.0.1:8765/rpc'))
    judge=Judge();gateway=Gateway()
    app=create_app(config,store=ObservationStore(tmp_path),judge=judge,gateway=gateway,qq_secret='secret')
    with TestClient(app) as client:
        for n in (1,2,3):
            body=json.dumps(dict(post_type='message',self_id=10000,time=1,message_id=n,
                message_type='group',group_id=42,user_id=n+10,message=f'part{n}')).encode()
            headers={'X-Self-ID':'10000','X-Signature':'sha1='+hmac.new(b'secret',body,hashlib.sha1).hexdigest()}
            assert client.post('/onebot/events',content=body,headers=headers).status_code==204
        client.portal.call(app.state.queue.join)
        assert judge.calls==0
        client.portal.call(app.state.scheduler.check_due,app.state.scheduler.clock()+6)
        client.portal.call(app.state.scheduler.ready.join)
        assert judge.calls==1 and len(gateway.prompts)==1
        assert all(f'part{n}' in gateway.prompts[0] for n in (1,2,3))
