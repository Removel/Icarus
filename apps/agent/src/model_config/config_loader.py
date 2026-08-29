import json
import os
from importlib.resources import files
from importlib.resources.abc import Traversable

from apps.agent.src.model_config.config_model import ConfigModel
from packages.runtime_environment import load_icarus_environment

load_icarus_environment()


def _settings_resource() -> Traversable:
    return files("apps.agent").joinpath("settings.json")


def get_config() -> ConfigModel:
    with _settings_resource().open("r", encoding="utf-8") as f:
        data = json.load(f)

    data["openai_api_key"] = os.getenv("OPENAI_API_KEY", "")
    data["anthropic_api_key"] = os.getenv("ANTHROPIC_API_KEY", "")
    data["icarus_data_dir"] = os.getenv("ICARUS_DATA_DIR") or None

    return ConfigModel(**data)
