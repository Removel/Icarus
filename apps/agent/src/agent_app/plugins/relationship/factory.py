"""Construct the optional tool-only relationship plugin without health checks."""

import os
from urllib.parse import urlsplit

from icarus.runtime import PluginRegistration
from agent_app.plugins.relationship.config import RelationshipConfig
from agent_app.plugins.relationship.plugin import RelationshipPlugin
from agent_app.plugins.relationship.rela_mem_http_adapter import RelaMemHttpAdapter


def create_plugin(plugin_id, workspace_path, session_id, config, required_capabilities, logger):
    del workspace_path, session_id, required_capabilities, logger
    user_config = {key: value for key, value in config.items()
                   if key not in {"runtime", "identity", "task_channels", "_user_config"}}
    parsed = RelationshipConfig.model_validate(user_config)
    token = os.environ.get("RELA_MEM_API_TOKEN", "").strip()
    if not token and urlsplit(parsed.endpoint).hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("RELA_MEM_API_TOKEN is required for non-loopback relationship service")
    backend = RelaMemHttpAdapter(parsed.endpoint, api_token=token, timeout_seconds=parsed.timeout_seconds)
    try:
        return PluginRegistration(plugin=RelationshipPlugin(plugin_id, backend))
    except BaseException:
        backend.close()
        raise
