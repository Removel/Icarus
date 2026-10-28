"""Search transport composition; query decisions belong to the main Agent."""

import os

from agent_app.plugins.web_search.config import PluginConfig
from agent_app.plugins.web_search.http_backend import SearchHttpBackend
from agent_app.plugins.web_search.plugin import WebSearchPlugin
from icarus.runtime import PluginRegistration


def create_plugin(plugin_id, workspace_path, session_id, config, required_capabilities, logger):
    settings = PluginConfig.model_validate(config.get("_user_config", {}))
    backend = SearchHttpBackend(settings.endpoint, os.getenv(settings.api_key_env, ""))
    return PluginRegistration(plugin=WebSearchPlugin(plugin_id, backend=backend,
        task_channels=config.get("task_channels"), deadline_seconds=settings.deadline_seconds))
