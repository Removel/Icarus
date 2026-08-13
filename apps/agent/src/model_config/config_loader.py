import json
import os
from pathlib import Path

from dotenv import load_dotenv

from apps.agent.src.model_config.config_model import ConfigModel

load_dotenv()


def get_config() -> ConfigModel:
    settings_path = Path(__file__).parent.parent.parent / "settings.json"
    with open(settings_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    data["openai_api_key"] = os.getenv("OPENAI_API_KEY", "")
    data["anthropic_api_key"] = os.getenv("ANTHROPIC_API_KEY", "")

    return ConfigModel(**data)
