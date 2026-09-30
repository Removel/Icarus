import asyncio
from apps.argus.src.clients.icarus_client import IcarusClient


def test_steer_rpc_parameters_and_negative_receipt():
    async def exercise():
        client=IcarusClient('ws://127.0.0.1:8765/rpc');seen=[]
        async def call(method,params):seen.append((method,params));return {'task_id':'t','status':'not_running'}
        client.call=call
        result=await client.session_steer('/work','s','t','more','b:steer:t')
        assert result['status']=='not_running'
        assert seen==[('session.steer',dict(workspace_path='/work',session_id='s',task_id='t',prompt='more',submission_id='b:steer:t'))]
    asyncio.run(exercise())
