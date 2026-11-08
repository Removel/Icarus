"""Missing required observation contribution must stop the production Session before LLM."""
import asyncio

import httpx
import pytest

from agent_app.application import AgentRuntime
from agent_app.contracts.prompt import PromptContributionEvent
from agent_app.plugins.argus.plugin import ArgusPlugin
from icarus.core.contracts.input import UserInputEvent
from icarus.core.plugin import handles
from apps.gateway.src.app import create_app
from apps.gateway.test.test_runtime_e2e import config_for, local_provider, ProviderHandler
from apps.agent.test.agent_app.plugins.argus.test_prompt_delivery import valid_wake


@pytest.mark.parametrize('failure',['failed','timeout'])
def test_required_observation_failure_never_calls_the_model(tmp_path,monkeypatch,failure):
    monkeypatch.setenv('ARGUS_GATEWAY_TOKEN','synthetic')
    monkeypatch.setenv('ARGUS_WORKSPACE_PATH',str(tmp_path/'workspace'))
    @handles(UserInputEvent)
    async def no_body(self,source,event):
        if failure=='failed':
            await self.publish(PromptContributionEvent(task_id=event.task_id,status='failed',error='synthetic missing body'))
        # Otherwise deliberately omit the contribution and let the real deadline fire.
    monkeypatch.setattr(ArgusPlugin,'on_input',no_body)
    async def run(port):
        config=config_for(tmp_path/'data',port)
        config.runtime.plugin_config['prompt']={'contributor_timeout_seconds':.05}
        runtime=AgentRuntime(config_loader=lambda:config);app=create_app(runtime)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://gateway',headers={'Authorization':'Bearer synthetic'}) as client:
                selected=await client.post('/argus/session',json={'channel_id':'channel','request_id':'select','session_key':'slot'})
                payload=valid_wake() | {'session_id':selected.json()['session_id']}
                response=await client.post('/argus/wake',json=payload)
                assert response.status_code==200,response.text
                for _ in range(200):
                    state=(await client.post('/argus/status',json={'wake_id':'wake'})).json()
                    if state['status'] in {'failed','completed'}:break
                    await asyncio.sleep(.01)
                assert state['status']=='failed'
                assert not any(path.endswith('/chat/completions') for path,_ in ProviderHandler.requests)
    with local_provider() as port:asyncio.run(run(port))
