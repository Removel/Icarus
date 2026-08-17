import pytest

from apps.agent.src.model_config import (
    ConfigModel,
    EmbeddingSettings,
    LLMConfig,
    ModelSettings,
    ThinkMode,
)
from apps.agent.src.model_provider.embedding_factory import EmbeddingFactory
from apps.agent.src.model_provider.impl.fastembed_embedding import (
    FastEmbedEmbedding,
)


def make_config() -> ConfigModel:
    model = LLMConfig(
        model_name="test-model",
        max_tokens=1024,
        temperature=0,
        default_think_level=ThinkMode.LOW,
    )
    return ConfigModel(
        openai_base_url="https://openai.example.com/v1",
        anthropic_base_url="https://anthropic.example.com",
        embedding=EmbeddingSettings(
            provider="fastembed",
            model_name=(
                "sentence-transformers/"
                "paraphrase-multilingual-MiniLM-L12-v2"
            ),
        ),
        model_settings=ModelSettings(thinking=model, perception=model),
    )


def test_create_embedding_按配置创建fastembed适配器(monkeypatch, tmp_path):
    captured = {}

    def fake_init(self, settings, cache_dir):
        captured["settings"] = settings
        captured["cache_dir"] = cache_dir

    monkeypatch.setattr(FastEmbedEmbedding, "__init__", fake_init)

    embedding = EmbeddingFactory(make_config(), tmp_path).create_embedding()

    assert isinstance(embedding, FastEmbedEmbedding)
    assert captured["settings"].model_name == (
        "sentence-transformers/"
        "paraphrase-multilingual-MiniLM-L12-v2"
    )
    assert captured["cache_dir"] == tmp_path


def test_create_embedding_拒绝未知provider(tmp_path):
    config = make_config()
    config.embedding = config.embedding.model_copy(
        update={"provider": "unknown"}
    )

    with pytest.raises(ValueError, match="Unsupported embedding provider: unknown"):
        EmbeddingFactory(config, tmp_path).create_embedding()
