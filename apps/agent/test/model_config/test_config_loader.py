import json

import pydantic
import pytest

from apps.agent.src.model_config import config_loader
from apps.agent.src.model_config import ConfigModel, get_config


def test_get_config_读取模型配置并使用环境变量密钥(
    monkeypatch,
    tmp_path,
):
    settings = {
        "openai_base_url": "https://openai.example.com/v1",
        "anthropic_base_url": "https://anthropic.example.com",
        "embedding": {
            "provider": "fastembed",
            "model_name": (
                "sentence-transformers/"
                "paraphrase-multilingual-MiniLM-L12-v2"
            ),
        },
        "model_settings": {
            role: {
                "model_name": f"{role}-model",
                "max_tokens": 4096,
                "temperature": 0.2,
                "default_think_level": "medium",
            }
            for role in ("thinking", "perception")
        },
        "use_protocol": "anthropic",
    }
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(json.dumps(settings), encoding="utf-8")
    monkeypatch.setattr(
        config_loader,
        "Path",
        lambda _: tmp_path / "src/model_config/config_loader.py",
    )
    monkeypatch.setenv("OPENAI_API_KEY", "openai-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-key")

    config = get_config()

    assert config.openai_api_key == "openai-key"
    assert config.anthropic_api_key == "anthropic-key"
    assert config.use_protocol == "anthropic"
    assert config.embedding.provider == "fastembed"
    assert config.embedding.model_name == (
        "sentence-transformers/"
        "paraphrase-multilingual-MiniLM-L12-v2"
    )
    assert config.skill.minimum_content_score == 0.8
    assert config.model_settings.thinking.model_name == "thinking-model"
    assert config.model_settings.perception.max_tokens == 4096


def test_config_model_embedding为必填配置():
    with pytest.raises(pydantic.ValidationError, match="embedding"):
        ConfigModel(
            openai_base_url="https://openai.example.com/v1",
            anthropic_base_url="https://anthropic.example.com",
            model_settings={
                role: {
                    "model_name": f"{role}-model",
                    "max_tokens": 4096,
                    "temperature": 0.2,
                    "default_think_level": "medium",
                }
                for role in ("thinking", "perception")
            },
        )


@pytest.mark.parametrize("score", [-0.01, 1.01])
def test_config_model拒绝越界skill匹配门槛(score):
    with pytest.raises(pydantic.ValidationError, match="minimum_content_score"):
        ConfigModel(
            openai_base_url="https://openai.example.com/v1",
            anthropic_base_url="https://anthropic.example.com",
            embedding={
                "provider": "fastembed",
                "model_name": "model",
            },
            skill={"minimum_content_score": score},
            model_settings={
                role: {
                    "model_name": f"{role}-model",
                    "max_tokens": 4096,
                    "temperature": 0.2,
                    "default_think_level": "medium",
                }
                for role in ("thinking", "perception")
            },
        )
