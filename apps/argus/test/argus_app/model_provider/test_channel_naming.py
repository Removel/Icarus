"""One bounded, tool-free model request summarizes a new Channel's name."""
import importlib.util
import json

import pytest

from icarus.core.models.messages import LLMResponse, Message, TextPart, ToolCall
from argus_app.contracts.models import DomainError


class Model:
    def __init__(self, title='图片查看工具需求', response=None):
        self.calls=[]
        self.response=response or LLMResponse(Message('assistant',[TextPart(json.dumps({'title':title},ensure_ascii=False))]),finish_reason='stop')
    async def ainvoke(self,messages,tools=None):
        self.calls.append((messages,tools));return self.response


async def test_channel_name_is_summarized_without_tools_or_route_authority():
    assert importlib.util.find_spec('argus_app.model_provider.channel_naming'), 'missing Channel naming step'
    from argus_app.model_provider.channel_naming import ChannelNamer
    model=Model()
    title=await ChannelNamer(model)('有没有人能帮忙开发一个image view工具',reason='needs response')
    assert title=='图片查看工具需求'
    assert len(model.calls)==1 and model.calls[0][1] is None
    messages,_=model.calls[0]
    assert 'image view' in messages[1].content[0].text
    assert 'image view' not in messages[0].content[0].text


@pytest.mark.parametrize('title',['', 'x'*61, 'first\nsecond'])
async def test_invalid_title_is_rejected_not_silently_repaired(title):
    assert importlib.util.find_spec('argus_app.model_provider.channel_naming')
    from argus_app.model_provider.channel_naming import ChannelNamer
    with pytest.raises(DomainError,match='invalid_output'):
        await ChannelNamer(Model(title))('request',reason='response')


def test_configured_namer_reuses_perception_with_bounded_budget(monkeypatch):
    from argus_app.model_provider.channel_naming import configured_channel_namer
    from apps.argus.test.argus_app.model_provider.test_discovery_fallback import config
    monkeypatch.setattr('agent_app.config.get_config',lambda path:config())
    created=[]
    class Factory:
        def __init__(self, config):pass
        def create_llm(self,**kwargs):created.append(kwargs);return Model()
    monkeypatch.setattr('agent_app.model_provider.factory.LLMFactory',Factory)
    namer,owner=configured_channel_namer()
    assert namer.llm is owner
    assert created==[{'role':'perception','max_tokens':256,'max_retries':0}]


async def test_model_failure_is_sanitized_without_retry():
    from argus_app.model_provider.channel_naming import ChannelNamer
    class Failed:
        calls=0
        async def ainvoke(self,messages,tools=None):
            self.calls+=1
            raise RuntimeError('synthetic credential must not appear')
    model=Failed()
    with pytest.raises(DomainError,match='unavailable') as caught:
        await ChannelNamer(model)('request',reason='new matter')
    assert 'credential' not in str(caught.value) and model.calls==1


async def test_title_tool_calls_are_rejected():
    assert importlib.util.find_spec('argus_app.model_provider.channel_naming')
    from argus_app.model_provider.channel_naming import ChannelNamer
    response=LLMResponse(Message('assistant',[TextPart('{"title":"Title"}')],
        tool_calls=[ToolCall('call','unexpected',{})]),finish_reason='stop')
    with pytest.raises(DomainError,match='invalid_output'):
        await ChannelNamer(Model(response=response))('request',reason='response')
