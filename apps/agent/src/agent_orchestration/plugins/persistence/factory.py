from collections.abc import Mapping
from pathlib import Path

from apps.agent.src.agent_orchestration.plugin_runtime import (
    PluginRegistration,
    ProvidedCapability,
)
from apps.agent.src.agent_orchestration.plugins.persistence.plugin import (
    PersistencePlugin,
)
from apps.agent.src.agent_orchestration.plugins.persistence.runtime import (
    PersistenceRuntime,
)
from apps.agent.src.agent_orchestration.plugins.persistence.session_identity import (
    SessionIdentity,
)


def create_plugin(
    plugin_id: str,
    workspace_path: Path,
    session_id: str,
    config: Mapping[str, object],
    required_capabilities,
    logger,
) -> PluginRegistration:
    del required_capabilities
    data_dir = config.get("data_dir")
    if not isinstance(data_dir, (str, Path)):
        raise ValueError("persistence config requires data_dir")
    runtime = config.get("runtime") or PersistenceRuntime(
        data_dir, workspace_path
    )
    identity = SessionIdentity.create(workspace_path, session_id)
    hook_registry = config.get("hook_registry")
    if hook_registry is None:
        raise ValueError("persistence config requires hook_registry")
    plugin = PersistencePlugin(
        plugin_id, runtime, identity, logger, hook_registry
    )
    return PluginRegistration(
        plugin=plugin,
        capabilities=(
            ProvidedCapability("runtime", "1.0.0", runtime),
            ProvidedCapability("session", "1.0.0", identity),
            ProvidedCapability("state_store", "1.0.0", plugin),
            ProvidedCapability("redactor", "1.0.0", runtime.redactor),
        ),
    )
