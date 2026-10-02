import asyncio
from apps.argus.src.clients.icarus_client import IcarusClient


def test_steer_rpc_parameters_and_negative_receipt():
    async def exercise():
        client=IcarusClient('ws://127.0.0.1:8765/rpc');seen=[]
        async def call(method,params):seen.append((method,params));return {'task_id':'t','status':'not_running'}
        client.call=call
        result=await client.session_steer('/work','s','t','more','b:steer:t')
        assert result.kind=='rejected' and result.receipt['status']=='not_running'
        assert seen==[('session.steer',dict(workspace_path='/work',session_id='s',task_id='t',prompt='more',submission_id='b:steer:t'))]
    asyncio.run(exercise())


def test_history_pagination_keeps_high_watermark_and_sequence_gaps():
    async def exercise():
        client=IcarusClient('ws://127.0.0.1:8765/rpc')
        calls=[]
        async def call(method,params):
            calls.append(params['after_sequence'])
            if len(calls)==1:
                return dict(records=[{'sequence':5}],has_more=True,next_after_sequence=5,history_cursor=40)
            return dict(records=[{'sequence':40}],has_more=False,next_after_sequence=40,history_cursor=40)
        client.call=call
        result=await client.session_get_history('/work','s')
        assert calls==[0,5]
        assert result['history_cursor']==40 and not result['has_more']
        assert [r['sequence'] for r in result['records']]==[5,40]
    asyncio.run(exercise())
