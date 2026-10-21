"""All built-in plugins have a schema that keeps user values separate from injected resources."""

import importlib
from pathlib import Path
import pytest
from pydantic import ValidationError


def test_plugin_config_schemas_are_declared_for_all_builtins():
    plugins = Path(__file__).resolve().parents[3] / "src" / "agent_app" / "plugins"
    for path in plugins.glob("*/manifest.json"):
        module = importlib.import_module(f"agent_app.plugins.{path.parent.name}.config")
        assert hasattr(module, "PluginConfig"), path.parent.name


def test_memory_config_rejects_unknown_and_invalid_values():
    module = importlib.import_module("agent_app.plugins.memory.config")
    with pytest.raises(ValidationError):
        module.PluginConfig.model_validate({"user_id": "u", "agent_id": "a", "unknown": True})
    with pytest.raises(ValidationError):
        module.PluginConfig.model_validate({"user_id": "u", "agent_id": "a", "preserve_input_language": "yes"})
