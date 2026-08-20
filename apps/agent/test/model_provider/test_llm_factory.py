import pytest

from apps.agent.src.model_config import (
    ConfigModel,
    EmbeddingSettings,
    LLMConfig,
    ModelSettings,
    ThinkMode,
)
from apps.agent.src.model_provider.impl.anthropic_llm import AnthropicLLM
from apps.agent.src.model_provider.impl.openai_llm import OpenAILLM
from apps.agent.src.model_provider.llm_factory import LLMFactory


def make_config() -> ConfigModel:
    model = LLMConfig(
        model_name="test-model",
        max_tokens=4096,
        temperature=0.2,
        default_think_level=ThinkMode.MEDIUM,
    )
    return ConfigModel(
        openai_base_url="https://openai.example.com/v1",
        anthropic_base_url="https://anthropic.example.com",
        openai_api_key="openai-key",
        anthropic_api_key="anthropic-key",
        embedding=EmbeddingSettings(
            provider="fastembed",
            model_name=(
                "sentence-transformers/"
                "paraphrase-multilingual-MiniLM-L12-v2"
            ),
        ),
        model_settings=ModelSettings(
            thinking=model,
            perception=model,
        ),
        use_protocol="openai",
    )


def test_create_llm_按协议创建对应适配器():
    factory = LLMFactory(config=make_config())

    openai_llm = factory.create_llm(
        role="thinking",
        protocol="openai",
        model_name="gpt-test",
    )
    anthropic_llm = factory.create_llm(
        role="perception",
        protocol="anthropic",
        model_name="claude-test",
    )

    assert isinstance(openai_llm, OpenAILLM)
    assert isinstance(anthropic_llm, AnthropicLLM)
    assert openai_llm._client.api_key == "openai-key"
    assert anthropic_llm._client.api_key == "anthropic-key"
    assert openai_llm.max_tokens == 4096
    assert openai_llm.reasoning_effort == ThinkMode.MEDIUM
    assert anthropic_llm.max_tokens == 4096
    assert anthropic_llm.thinking_budget == 1024
    assert str(openai_llm._client.base_url) == "https://openai.example.com/v1/"
    assert str(anthropic_llm._client.base_url) == "https://anthropic.example.com"


def test_create_llm_不传参数时使用角色和全局配置():
    factory = LLMFactory(config=make_config())

    llm = factory.create_llm(role="perception")

    assert isinstance(llm, OpenAILLM)
    assert llm.model_name == "test-model"
    assert llm.max_tokens == 4096
    assert llm.temperature == 0.2
    assert llm.reasoning_effort == ThinkMode.MEDIUM


def test_create_llm_anthropic使用指定thinking_budget():
    factory = LLMFactory(config=make_config())

    llm = factory.create_llm(
        role="thinking",
        protocol="anthropic",
        thinking_budget=2048,
    )

    assert isinstance(llm, AnthropicLLM)
    assert llm.thinking_budget == 2048


def test_create_llm_openai不允许thinking_budget():
    factory = LLMFactory(config=make_config())

    with pytest.raises(ValueError, match="only supported by Anthropic"):
        factory.create_llm(role="thinking", thinking_budget=2048)


def test_init_未传配置时调用配置加载器(monkeypatch):
    expected = make_config()
    monkeypatch.setattr(
        "apps.agent.src.model_provider.llm_factory.get_config",
        lambda: expected,
    )

    factory = LLMFactory()

    assert factory.config is expected
