from apps.argus.test.persistence_fakes import TypedGateway
import asyncio
from datetime import UTC, datetime
import hashlib,hmac,json
from fastapi.testclient import TestClient
from apps.argus.src.config.config_model import ArgusConfig,QQConfig
from apps.argus.test.persistence_fakes import ObservationStore
from apps.argus.src.main import create_app
from apps.argus.src.clients.icarus_client import GatewayBusinessError


class Gateway:
    connected=True
    def __init__(self):self.sessions={};self.submissions=[]
    async def connect(self):pass
    async def close(self):pass
    async def session_get(self,workspace_path,session_id):
        if session_id not in self.sessions:raise GatewayBusinessError('session_not_found','missing')
        return self.sessions[session_id]
    async def session_create(self,workspace_path,session_id):
        self.sessions[session_id]=dict(workspace_key='k',session_id=session_id,lifecycle='ready',active_task_ids=[])
        return self.sessions[session_id]
    async def session_submit(self,**params):
        self.submissions.append(params);return {'task_id':'task','queue_position':0}


def test_private_batch_runs_without_jev_and_reports_batch_status(tmp_path):
    config=ArgusConfig(host='127.0.0.1',port=8766,log_level='INFO',queue_capacity=5,worker_count=1,
        qq=QQConfig(enabled=True,bot_id='10000',workspace_path=str(tmp_path),gateway_url='ws://127.0.0.1:8765/rpc'))
    gateway=Gateway();store=ObservationStore(tmp_path)
    app=create_app(config,database=store.database,judge=object(),gateway=TypedGateway(gateway),qq_secret='secret')
    body=json.dumps(dict(post_type='message',self_id=10000,time=1,message_id=411,message_type='private',user_id=7,message='很久以前的问题')).encode()
    headers={'X-Self-ID':'10000','X-Signature':'sha1='+hmac.new(b'secret',body,hashlib.sha1).hexdigest()}
    with TestClient(app) as client:
        assert client.post('/onebot/events',content=body,headers=headers).status_code==204
        client.portal.call(app.state.scheduler.check_due,app.state.scheduler.clock()+6)
        client.portal.call(app.state.scheduler.ready.join)
        assert len(gateway.submissions)==1
        rows=client.portal.call(store.recent,'qq','10000','qq:private:7')
        result=client.get(f'/observations/{rows[0].id}/status').json()
        assert result['status']=='submitted' and result['batch_id']
        assert client.post('/onebot/events',content=body,headers=headers).status_code==204
        assert len(gateway.submissions)==1
