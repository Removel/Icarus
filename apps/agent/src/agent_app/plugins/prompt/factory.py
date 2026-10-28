"""Construct the Prompt plugin from explicit application facilities."""

from pathlib import Path

from agent_app.plugins.prompt.base_prompt import DEFAULT_SYSTEM_PROMPT
from agent_app.plugins.prompt.config import PluginConfig
from agent_app.plugins.prompt.plugin import PromptPlugin
from icarus.core.registration import PluginRegistration, ProvidedCapability


def create_plugin(plugin_id, workspace_path, session_id, config, required_capabilities, logger):
    settings = PluginConfig.model_validate(config.get("_user_config", {}))
    plugin = PromptPlugin(plugin_id, workspace_path=Path(workspace_path), config=settings,
                          instructions=config.get("instructions"), task_channels=config["task_channels"],
                          logger=logger, base_prompt=config.get("base_prompt", DEFAULT_SYSTEM_PROMPT))
    return PluginRegistration(plugin=plugin, capabilities=(ProvidedCapability("prompt.read", "1.0.0", plugin),))
