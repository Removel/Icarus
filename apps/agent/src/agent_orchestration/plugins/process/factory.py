"""Construct ProcessPlugin from persistence capabilities."""

from collections.abc import Mapping
from pathlib import Path

from apps.agent.src.agent_orchestration.plugin_runtime import PluginRegistration
from apps.agent.src.agent_orchestration.plugins.persistence import (
    PersistenceRuntime,
    SessionIdentity,
)
from apps.agent.src.agent_orchestration.plugins.process.config import ProcessConfig
from apps.agent.src.agent_orchestration.plugins.process.plugin import ProcessPlugin
from apps.agent.src.agent_orchestration.plugins.process.tools import (
    BackgroundProcessTool,
)


def create_plugin(
    plugin_id: str,
    workspace_path: Path,
    session_id: str,
    config: Mapping[str, object],
    required_capabilities,
    logger,
) -> PluginRegistration:
    del logger
    runtime = required_capabilities.get(("persistence", "runtime"))
    identity = required_capabilities.get(("persistence", "session"))
    if not isinstance(runtime, PersistenceRuntime):
        raise ValueError("process requires persistence runtime")
    if not isinstance(identity, SessionIdentity):
        raise ValueError("process requires persistence session identity")
    if identity.session_id != session_id:
        raise ValueError("process persistence session does not match runtime session")
    process_config = ProcessConfig.model_validate(dict(config))
    runtime.resolver.ensure_session(identity)
    plugin = ProcessPlugin(
        plugin_id,
        session_id=identity.session_id,
        workspace_path=workspace_path,
        processes_dir=runtime.resolver.processes_dir(identity),
        config=process_config,
    )
    return PluginRegistration(
        plugin=plugin,
        tools=(BackgroundProcessTool(plugin),),
    )
