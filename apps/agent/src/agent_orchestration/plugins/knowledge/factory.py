"""Construct KnowledgePlugin from simple configuration."""

from __future__ import annotations

import os
from urllib.parse import urlsplit

from apps.agent.src.agent_orchestration.plugin_runtime import PluginRegistration
from apps.agent.src.agent_orchestration.plugins.knowledge.openkb_http_adapter import (
    OpenKBHttpAdapter,
)
from apps.agent.src.agent_orchestration.plugins.knowledge.plugin import KnowledgePlugin
from apps.agent.src.agent_orchestration.plugins.knowledge.tools import (
    create_knowledge_tools,
)


def create_plugin(
    plugin_id, workspace_path, session_id, config, required_capabilities, logger,
):
    del session_id, required_capabilities, logger
    allowed = {
        "knowledge_base", "backend", "endpoint",
        "max_file_bytes", "max_request_bytes",
    }
    unknown = set(config) - allowed
    if unknown:
        raise ValueError(
            "knowledge config has unknown fields: " + ", ".join(sorted(unknown))
        )
    knowledge_base = _required_string(config, "knowledge_base")
    if config.get("backend", "openkb_http") != "openkb_http":
        raise ValueError("knowledge backend must be openkb_http")
    endpoint = config.get("endpoint", "http://127.0.0.1:7566")
    if not isinstance(endpoint, str) or not endpoint.strip():
        raise ValueError("knowledge endpoint must be a non-empty string")
    api_token = os.getenv("ICARUS_OPENKB_API_TOKEN", "")
    parsed_endpoint = urlsplit(endpoint)
    if parsed_endpoint.scheme not in {"http", "https"} or not parsed_endpoint.hostname:
        raise ValueError("knowledge endpoint must be an absolute http or https URL")
    host = parsed_endpoint.hostname
    if not api_token and host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("ICARUS_OPENKB_API_TOKEN is required for non-loopback OpenKB")
    if parsed_endpoint.scheme != "https" and host not in {
        "127.0.0.1", "localhost", "::1"
    }:
        raise ValueError("knowledge endpoint must use https for non-loopback OpenKB")
    max_file_bytes = _positive_integer(
        config, "max_file_bytes", 100 * 1024 * 1024
    )
    max_request_bytes = _positive_integer(
        config, "max_request_bytes", 500 * 1024 * 1024
    )
    backend = OpenKBHttpAdapter(
        endpoint, knowledge_base=knowledge_base, api_token=api_token
    )
    try:
        plugin = KnowledgePlugin(
            plugin_id, backend, workspace_path=workspace_path,
            max_file_bytes=max_file_bytes,
            max_request_bytes=max_request_bytes,
        )
    except BaseException:
        backend.close()
        raise
    return PluginRegistration(plugin=plugin, tools=create_knowledge_tools(plugin))


def _required_string(config, name: str) -> str:
    value = config.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"knowledge config requires {name}")
    return value.strip()


def _positive_integer(config, name: str, default: int) -> int:
    value = config.get(name, default)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"knowledge {name} must be a positive integer")
    return value
