"""Construct MemoryPlugin from Manifest dependencies and simple config."""

from __future__ import annotations

import os
from urllib.parse import urlsplit

from apps.agent.src.agent_orchestration.plugin_runtime import PluginRegistration
from apps.agent.src.agent_orchestration.plugins.blackboard import (
    RegionDefinition,
    RegionRegistry,
)
from apps.agent.src.agent_orchestration.plugins.memory.mem0_http_adapter import Mem0HttpAdapter
from apps.agent.src.agent_orchestration.plugins.memory.plugin import MemoryPlugin
from apps.agent.src.agent_orchestration.plugins.memory.tools import create_memory_tools
from apps.agent.src.agent_orchestration.plugins.persistence import SessionIdentity


def create_plugin(
    plugin_id, workspace_path, session_id, config, required_capabilities, logger,
):
    del workspace_path, session_id, logger
    allowed = {"user_id", "agent_id", "backend", "endpoint", "recall"}
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
    recall = config.get("recall", {})
    if not isinstance(recall, dict):
        raise ValueError("memory recall config must be an object")
    recall_allowed = {"top_k", "threshold", "max_context_chars", "deadline_ms"}
    recall_unknown = set(recall) - recall_allowed
    if recall_unknown:
        raise ValueError("memory recall config has unknown fields: " + ", ".join(sorted(recall_unknown)))
    identity = required_capabilities[("persistence", "session")]
    registry = required_capabilities[("blackboard", "region_registry")]
    if not isinstance(identity, SessionIdentity):
        raise ValueError("memory requires persistence session identity")
    if not isinstance(registry, RegionRegistry):
        raise ValueError("memory requires Blackboard Region Registry")
    top_k = recall.get("top_k", 3)
    threshold = recall.get("threshold", 0.65)
    max_context_chars = recall.get("max_context_chars", 6000)
    deadline_ms = recall.get("deadline_ms", 1000)
    backend = Mem0HttpAdapter(
        endpoint, user_id=user_id, agent_id=agent_id, api_key=api_key,
        timeout_seconds=max(
            0.05, deadline_ms / 1000 - 0.02
        ),
    )
    try:
        plugin = MemoryPlugin(
            plugin_id, backend, workspace_key=identity.workspace_key,
            user_id=user_id, agent_id=agent_id, session_id=identity.session_id,
            top_k=top_k, threshold=threshold,
            max_context_chars=max_context_chars, deadline_ms=deadline_ms,
        )
        registration = PluginRegistration(
            plugin=plugin, tools=create_memory_tools(plugin)
        )
        handle = registry.register(
            RegionDefinition(
                region="memory", owner_plugin_id=plugin_id, lifetime="input",
                required_for_start=True, auto_expose=True,
                allowed_statuses=("idle", "recalling"), initial_status="idle",
                max_summary_chars=500, max_refs=top_k,
                max_data_chars=max_context_chars,
            )
        )
    except BaseException:
        backend.close()
        raise
    plugin.region_registration = handle
    return registration


def _required_string(config, name: str) -> str:
    value = config.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"memory config requires {name}")
    return value.strip()
