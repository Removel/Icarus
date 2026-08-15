import json

from apps.agent.src.model_config import config_loader
from apps.agent.src.model_config import get_config


def test_get_config_读取模型配置并使用环境变量密钥(
    monkeypatch,
    tmp_path,
):
    settings = {
        "openai_base_url": "https://openai.example.com/v1",
        "anthropic_base_url": "https://anthropic.example.com",
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
    assert config.model_settings.thinking.model_name == "thinking-model"
    assert config.model_settings.perception.max_tokens == 4096
