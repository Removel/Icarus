"""Construct MemoryPlugin from Manifest dependencies and simple config."""

from __future__ import annotations

import os
from urllib.parse import urlsplit

from icarus.runtime import PluginRegistration
from icarus.core.contracts.context import RegionDefinition
from icarus.core.identity import SessionIdentity
from agent_app.plugins.memory.mem0_http_adapter import Mem0HttpAdapter
from agent_app.plugins.memory.plugin import (
    DEFAULT_RECALL_DEADLINE_MS,
    MemoryPlugin,
)
from agent_app.plugins.memory.tools import create_memory_tools
from icarus.core.identity import SessionIdentity


MEMORY_TOOL_TIMEOUT_SECONDS = 60


def create_plugin(
    plugin_id, workspace_path, session_id, config, required_capabilities, logger,
):
    del workspace_path, session_id, logger
    identity = config["identity"]
    config = {key: value for key, value in config.items() if key not in {"runtime", "identity", "task_channels", "_user_config"}}
    allowed = {
        "user_id", "agent_id", "backend", "endpoint",
        "preserve_input_language", "recall",
    }
    unknown = set(config) - allowed
    if unknown:
        raise ValueError("memory config has unknown fields: " + ", ".join(sorted(unknown)))
    user_id = _required_string(config, "user_id")
    agent_id = _required_string(config, "agent_id")
    backend_name = config.get("backend", "mem0_http")
    if backend_name != "mem0_http":
        raise ValueError("memory backend must be mem0_http")
    endpoint = config.get("endpoint", "http://127.0.0.1:8888")
    if not isinstance(endpoint, str) or not endpoint.strip():
        raise ValueError("memory endpoint must be a non-empty string")
    api_key = os.getenv("ICARUS_MEM0_API_KEY", "")
    host = urlsplit(endpoint).hostname
    if not api_key and host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("ICARUS_MEM0_API_KEY is required for non-loopback Mem0")
    preserve_input_language = config.get("preserve_input_language", True)
    if not isinstance(preserve_input_language, bool):
        raise ValueError("memory preserve_input_language must be a boolean")
    recall = config.get("recall", {})
    if not isinstance(recall, dict):
        raise ValueError("memory recall config must be an object")
    recall_allowed = {"top_k", "threshold", "max_context_chars", "deadline_ms"}
    recall_unknown = set(recall) - recall_allowed
    if recall_unknown:
        raise ValueError("memory recall config has unknown fields: " + ", ".join(sorted(recall_unknown)))
    if not isinstance(identity, SessionIdentity):
        raise ValueError("memory requires session identity")
    top_k = recall.get("top_k", 3)
    threshold = recall.get("threshold", 0.25)
    max_context_chars = recall.get("max_context_chars", 6000)
    deadline_ms = recall.get("deadline_ms", DEFAULT_RECALL_DEADLINE_MS)
    backend = Mem0HttpAdapter(
        endpoint, user_id=user_id, agent_id=agent_id, api_key=api_key,
        preserve_input_language=preserve_input_language,
        timeout_seconds=MEMORY_TOOL_TIMEOUT_SECONDS,
    )
    try:
        plugin = MemoryPlugin(
            plugin_id, backend, workspace_key=identity.workspace_key,
            user_id=user_id, agent_id=agent_id, session_id=identity.session_id,
            top_k=top_k, threshold=threshold,
            max_context_chars=max_context_chars, deadline_ms=deadline_ms,
        )
        plugin._tools = tuple(create_memory_tools(plugin))
        registration = PluginRegistration(
            plugin=plugin, tools=()
        )
        plugin.region_definition = RegionDefinition(
            region="memory", owner_plugin_id=plugin_id, lifetime="input",
            required_for_start=True, auto_expose=True,
            allowed_statuses=("idle", "recalling"), initial_status="idle",
            max_summary_chars=500, max_refs=top_k, max_data_chars=max_context_chars,
        )
    except BaseException:
        backend.close()
        raise
    return registration


def _required_string(config, name: str) -> str:
    value = config.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"memory config requires {name}")
    return value.strip()
