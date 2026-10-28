
from agent_app.utils.redactor import Redactor
from collections.abc import Mapping
from pathlib import Path

from icarus.runtime import (
    PluginRegistration,
)
from icarus.core.identity import SessionIdentity
from agent_app.plugins.runtime_update.plugin import (
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
    del logger
    publisher = config.get("publish_update")
    if not callable(publisher):
        raise ValueError("runtime-update config requires publish_update")
    identity = SessionIdentity.create(workspace_path, session_id)
    redactor = Redactor()
    if not isinstance(redactor, Redactor):
        raise ValueError("runtime-update requires persistence redactor")
    return PluginRegistration(
        plugin=RuntimeUpdatePlugin(
            plugin_id,
            workspace_key=identity.workspace_key,
            session_id=identity.session_id,
            publish_update=publisher,
            redactor=redactor,
        )
    )
