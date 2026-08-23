from apps.agent.src.agent_orchestration.plugin_runtime import (
    PluginRegistration,
    ProvidedCapability,
)
from apps.agent.src.agent_orchestration.plugins.persistence.runtime import (
    PersistenceSession,
)
from apps.agent.src.agent_orchestration.plugins.user_input.plugin import (
    UserInputPlugin,
)


def create_plugin(
    plugin_id, workspace_path, session_id, config, required_capabilities,
    logger,
):
    del workspace_path, session_id, config, logger
    runtime = required_capabilities[("persistence", "runtime")]
    identity = required_capabilities[("persistence", "session")]
    task_channels = required_capabilities[("agent", "task_channels")]
    session = PersistenceSession(runtime, identity)
    plugin = UserInputPlugin(
        plugin_id, session, task_channels=task_channels
    )
    return PluginRegistration(
        plugin=plugin,
        capabilities=(
            ProvidedCapability("input", "1.0.0", plugin),
        ),
    )
