"""Load Argus settings from its application package."""

import json
import os
from importlib.resources import files

from packages.runtime_environment import load_icarus_environment

from .config_model import ArgusConfig


def load_config() -> ArgusConfig:
    load_icarus_environment()
    with files("apps.argus").joinpath("settings.json").open("r", encoding="utf-8") as stream:
        config = ArgusConfig.model_validate(json.load(stream))
    if config.qq.enabled and not os.getenv("ARGUS_QQ_REPORT_SECRET"):
        raise ValueError("ARGUS_QQ_REPORT_SECRET is required when QQ is enabled")
    return config
