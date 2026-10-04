import asyncio

import pytest

from apps.agent.src.application.session_title import generate_title
from apps.agent.src.model_provider.types import LLMResponse, Message, TextPart


@pytest.mark.parametrize('output, expected', [('“整理项目文档”\n解释', '整理项目文档'), ('x' * 100, 'x' * 80)])
def test_generate_title_uses_provider_and_closes_client(monkeypatch, output, expected):
    state = {}
    class Model:
        async def ainvoke(self, messages):
            state['messages'] = messages
            return LLMResponse(Message('assistant', [TextPart(output)]))
        async def aclose(self):
            state['closed'] = True
    class Factory:
        def __init__(self, config):
            state['config'] = config
        def create_llm(self, role):
            state['role'] = role
            return Model()
    monkeypatch.setattr('apps.agent.src.application.session_title.LLMFactory', Factory)
    config = object()
    assert asyncio.run(generate_title(config, 'a' * 5000)) == expected
    assert state['closed']
    assert state['config'] is config and state['role'] == 'perception'
    assert state['messages'][1].content == [TextPart('a' * 4000)]


def test_generate_title_rejects_empty_output_and_closes_client(monkeypatch):
    closed = []
    class Model:
        async def ainvoke(self, messages):
            return LLMResponse(Message('assistant', []))
        async def aclose(self):
            closed.append(True)
    class Factory:
        def __init__(self, config):
            pass
        def create_llm(self, role):
            return Model()
    monkeypatch.setattr('apps.agent.src.application.session_title.LLMFactory', Factory)
    with pytest.raises(ValueError, match='empty'):
        asyncio.run(generate_title(object(), 'message'))
    assert closed == [True]
