import json
import os
from importlib.resources import files
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Any, Mapping

from agent_app.config.config_model import ConfigModel
from agent_app.config.environment import load_icarus_environment, repository_root

load_icarus_environment()


def _settings_resource() -> Traversable | Path:
    root = repository_root()
    if root is not None:
        return root / "apps" / "agent" / "settings.json"
    return files("agent_app.resources").joinpath("default_settings.json")


def _apply_endpoint_overrides(data: dict[str, Any], environ: Mapping[str, str]) -> None:
    for variable, plugin_id in (
        ("ICARUS_MEM0_ENDPOINT", "memory"),
        ("ICARUS_OPENKB_ENDPOINT", "knowledge"),
        ("ICARUS_RELATIONSHIP_ENDPOINT", "relationship"),
    ):
        if variable in environ:
            plugins = data.setdefault("runtime", {}).setdefault("plugin_config", {})
            plugins.setdefault(plugin_id, {})["endpoint"] = environ[variable]


def get_config(settings_path: str | Path | None = None) -> ConfigModel:
    explicit = settings_path or os.getenv("ICARUS_SETTINGS_PATH")
    resource = Path(explicit).expanduser() if explicit is not None else _settings_resource()
    with resource.open("r", encoding="utf-8") as file:
        data = json.load(file)
    data["openai_api_key"] = os.getenv("OPENAI_API_KEY", "")
    data["anthropic_api_key"] = os.getenv("ANTHROPIC_API_KEY", "")
    data["icarus_data_dir"] = os.getenv("ICARUS_DATA_DIR") or None
    _apply_endpoint_overrides(data, os.environ)
    return ConfigModel(**data)
