from collections.abc import Mapping
from pathlib import Path

from apps.agent.src.agent_orchestration.plugin_runtime import (
    PluginRegistration,
)
from apps.agent.src.agent_orchestration.plugins.persistence import (
    SessionIdentity,
)
from apps.agent.src.agent_orchestration.plugins.runtime_update.plugin import (
    RuntimeUpdatePlugin,
)


def create_plugin(
    plugin_id: str,
    workspace_path: Path,
    session_id: str,
    config: Mapping[str, object],
    required_capabilities,
    logger,
) -> PluginRegistration:
    del required_capabilities, logger
    publisher = config.get("publish_update")
    if not callable(publisher):
        raise ValueError("runtime-update config requires publish_update")
    identity = SessionIdentity.create(workspace_path, session_id)
    return PluginRegistration(
        plugin=RuntimeUpdatePlugin(
            plugin_id,
            workspace_key=identity.workspace_key,
            session_id=identity.session_id,
            publish_update=publisher,
        )
    )
