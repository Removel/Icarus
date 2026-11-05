"""The production memory client sends distinct instance scopes to a shared backend."""
import asyncio
import json

import httpx

from agent_app.plugins.memory.mem0_http_adapter import Mem0HttpAdapter


def test_independent_instances_do_not_use_the_same_memory_scope():
    async def run():
        requests=[]
        def handle(request):
            payload=json.loads(request.content);requests.append((request.url.path,payload))
            return httpx.Response(200,json={'results':[]})
        transport=httpx.MockTransport(handle)
        async with httpx.AsyncClient(transport=transport,base_url='http://memory.test') as client:
            first=Mem0HttpAdapter('http://memory.test',user_id='owner',agent_id='instance-a',async_client=client)
            second=Mem0HttpAdapter('http://memory.test',user_id='owner',agent_id='instance-b',async_client=client)
            try:
                for adapter in (first,second):
                    await adapter.arecall('same QQ group',workspace_key='same-workspace',scope=None,
                        include_stopped=False,top_k=3,threshold=.1)
            finally:
                first.close();second.close()
        assert [payload['filters']['agent_id'] for _,payload in requests]==['instance-a','instance-b']
        assert all(payload['filters']['user_id']=='owner' for _,payload in requests)
        assert all(payload['filters']['OR']==[{'run_id':'global'},{'run_id':'workspace:same-workspace'}] for _,payload in requests)
    asyncio.run(run())
