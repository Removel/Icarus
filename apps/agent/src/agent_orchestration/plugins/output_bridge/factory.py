from apps.agent.src.agent_orchestration.plugin_runtime import (
    PluginRegistration,
    ProvidedCapability,
)
from apps.agent.src.application.output_bridge import OutputBridgePlugin


def create_plugin(
    plugin_id, workspace_path, session_id, config, required_capabilities,
    logger,
):
    del workspace_path, session_id, required_capabilities
    del logger
    plugin = config.get("plugin") or OutputBridgePlugin(plugin_id)
    return PluginRegistration(
        plugin=plugin,
        capabilities=(
            ProvidedCapability("output_subscription", "1.0.0", plugin),
        ),
    )
