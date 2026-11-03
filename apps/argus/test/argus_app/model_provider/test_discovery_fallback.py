import asyncio
import importlib
import importlib.util
import json

import pytest

from argus_app.contracts.models import DomainError
from icarus.core.models.messages import ImagePart, LLMResponse, Message, TextPart, ToolCall


def module():
    assert importlib.util.find_spec('argus_app.model_provider.discovery_fallback') is not None, 'one-shot fallback is missing'
    return importlib.import_module('argus_app.model_provider.discovery_fallback')


class LLM:
    def __init__(self, text=None, *, error=None, response=None):
        self.text = text or '{"action":"new","reason":"明确请求跟进"}'
        self.error, self.response, self.calls = error, response, []

    async def ainvoke(self, messages, tools=None):
        self.calls.append((messages, tools))
        if self.error:
            raise self.error
        return self.response or LLMResponse(Message('assistant', [TextPart(self.text)]), finish_reason='stop')


@pytest.mark.parametrize('decision', [
    {'action': 'new', 'reason': '明确请求'},
    {'action': 'existing', 'channel_id': 'channel-1', 'reason': '现有事项续接'},
    {'action': 'ignore', 'reason': '只是闲聊'},
    {'action': 'defer', 'reason': '无法确定指的是哪个事项'},
])
async def test_one_shot_returns_validated_decision_without_tools(decision):
    llm = LLM(json.dumps(decision, ensure_ascii=False))
    value = await module().DiscoveryFallback(llm)('请跟进此事', [{'id': 'channel-1', 'goal': '评审'}])
    assert value.model_dump(exclude_none=True) == decision
    assert len(llm.calls) == 1
    messages, tools = llm.calls[0]
    assert tools is None
    assert [m.role for m in messages] == ['system', 'user']
    data = json.loads(messages[1].content[0].text)
    assert data == {'text': '请跟进此事', 'candidates': [{'id': 'channel-1', 'goal': '评审'}]}
    assert '请跟进此事' not in messages[0].content[0].text


@pytest.mark.parametrize('text', [
    'not json', '[]', '{"action":"new"}',
    '{"action":"existing","channel_id":"outside","reason":"claimed"}',
    '{"action":"new","channel_id":"channel-1","reason":"invalid identity"}',
    '{"action":"new","reason":"ok","confidence":0.99}',
    '{"action":"new","reason":"ok","action":"ignore"}',
    json.dumps({'action': 'new', 'reason': 'x' * 513}),
    '{"action":"ignore","reason":""}',
])
async def test_invalid_output_is_not_repaired_or_converted_to_ignore(text):
    llm = LLM(text)
    with pytest.raises(DomainError) as error:
        await module().DiscoveryFallback(llm)('data', [{'id': 'channel-1'}])
    assert error.value.code == 'invalid_output'
    assert len(llm.calls) == 1


@pytest.mark.parametrize('response', [
    LLMResponse(Message('assistant', [TextPart('{"action":"new","reason":"ok"}')]), finish_reason='length'),
    LLMResponse(Message('assistant', [ImagePart('https://example.invalid/image.png')])),
    LLMResponse(Message('assistant', [TextPart('{"action":"new","reason":"ok"}')],
                        tool_calls=[ToolCall('call', 'unexpected_tool', {})])),
])
async def test_truncated_or_tool_response_is_rejected(response):
    llm = LLM(response=response)
    with pytest.raises(DomainError) as error:
        await module().DiscoveryFallback(llm)('data', [])
    assert error.value.code == 'invalid_output'
    assert len(llm.calls) == 1


@pytest.mark.parametrize('error,code', [(TimeoutError(), 'timeout'), (RuntimeError('synthetic secret'), 'unavailable')])
async def test_failed_model_call_preserves_error_not_ignore(error, code):
    llm = LLM(error=error)
    with pytest.raises(DomainError) as failure:
        await module().DiscoveryFallback(llm)('data', [])
    assert failure.value.code == code
    assert 'synthetic secret' not in str(failure.value)
    assert len(llm.calls) == 1


async def test_cancellation_propagates_without_another_call():
    llm = LLM(error=asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        await module().DiscoveryFallback(llm)('data', [])
    assert len(llm.calls) == 1


async def test_slow_call_is_bounded(monkeypatch):
    m = module()
    monkeypatch.setattr(m, 'CALL_TIMEOUT_SECONDS', .01)
    calls = []
    class Slow:
        async def ainvoke(self, messages, tools=None):
            calls.append(messages)
            await asyncio.Event().wait()
    with pytest.raises(DomainError) as error:
        await m.DiscoveryFallback(Slow())('data', [])
    assert error.value.code == 'timeout'
    assert len(calls) == 1


def config():
    from agent_app.config import ConfigModel, LLMConfig, ModelSettings, ThinkMode
    model = LLMConfig(model_name='synthetic-model', context_window=32000,
                      max_tokens=4096, temperature=.2, default_think_level=ThinkMode.LOW)
    return ConfigModel(openai_base_url='https://example.invalid/v1',
        anthropic_base_url='https://example.invalid', openai_api_key='synthetic-key',
        model_settings=ModelSettings(thinking=model, perception=model))


def test_configured_helper_uses_named_model_and_zero_retries(monkeypatch):
    m = module()
    assert callable(getattr(m, 'configured_discovery_fallback', None)), 'configured fallback builder is missing'
    from agent_app.model_provider.factory import LLMFactory
    calls = []
    llm = LLM()
    monkeypatch.setattr('agent_app.config.get_config', lambda path=None: config())
    def create(self, **kwargs):
        calls.append(kwargs)
        return llm
    monkeypatch.setattr(LLMFactory, 'create_llm', create)
    backend, owner = m.configured_discovery_fallback('perception')
    assert isinstance(backend, m.DiscoveryFallback)
    assert owner is llm
    assert calls == [{'role': 'perception', 'max_tokens': 2048, 'max_retries': 0}]


def test_configured_helper_rejects_missing_model_or_credentials(monkeypatch):
    m = module()
    assert callable(getattr(m, 'configured_discovery_fallback', None)), 'configured fallback builder is missing'
    settings = config()
    settings.openai_api_key = ''
    monkeypatch.setattr('agent_app.config.get_config', lambda path=None: settings)
    with pytest.raises(ValueError, match='credentials'):
        m.configured_discovery_fallback('perception')
    settings.openai_api_key = 'synthetic-key'
    with pytest.raises(ValueError):
        m.configured_discovery_fallback('missing-model')


@pytest.mark.parametrize('candidates', [[{'id': 'x'}] * 21, [{'id': 'x'}, {'id': 'x'}], [{'id': 'new'}], ['invalid']])
async def test_invalid_candidates_fail_before_call(candidates):
    llm = LLM()
    with pytest.raises(ValueError):
        await module().DiscoveryFallback(llm)('data', candidates)
    assert not llm.calls
