from icarus.runtime import (
    PluginRegistration,
    ProvidedCapability,
)
from icarus.runtime.assets import (
    PersistenceSession,
)
from agent_app.plugins.user_input.plugin import (
    UserInputPlugin,
)


def create_plugin(
    plugin_id, workspace_path, session_id, config, required_capabilities,
    logger,
):
    del workspace_path, session_id, logger
    runtime = config["runtime"]
    identity = config["identity"]
    task_channels = config["task_channels"]
    session = PersistenceSession(runtime, identity)
    plugin = UserInputPlugin(
        plugin_id, session, task_channels=task_channels
    )
    return PluginRegistration(
        plugin=plugin,
        capabilities=(
            ProvidedCapability("input.submit", "1.0.0", plugin),
        ),
    )
