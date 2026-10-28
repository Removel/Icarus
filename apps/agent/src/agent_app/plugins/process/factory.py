"""Construct ProcessPlugin from persistence capabilities."""

from collections.abc import Mapping
from pathlib import Path

from icarus.runtime import PluginRegistration
from icarus.runtime.assets import PersistenceRuntime
from icarus.core.identity import SessionIdentity
from agent_app.plugins.process.config import ProcessConfig
from agent_app.plugins.process.plugin import ProcessPlugin
from agent_app.plugins.process.tools import (
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
    runtime = config.get("runtime")
    identity = config.get("identity")
    if not isinstance(runtime, PersistenceRuntime):
        raise ValueError("process requires persistence runtime")
    if not isinstance(identity, SessionIdentity):
        raise ValueError("process requires persistence session identity")
    if identity.session_id != session_id:
        raise ValueError("process persistence session does not match runtime session")
    config = {key: value for key, value in config.items() if key not in {"runtime", "identity", "task_channels", "_user_config"}}
    process_config = ProcessConfig.model_validate(dict(config))
    runtime.resolver.ensure_session(identity)
    plugin = ProcessPlugin(
        plugin_id,
        session_id=identity.session_id,
        workspace_path=workspace_path,
        processes_dir=runtime.resolver.processes_dir(identity),
        config=process_config,
    )
    plugin._tools = tuple((BackgroundProcessTool(plugin),))
    return PluginRegistration(
        plugin=plugin,
        tools=(),
    )
