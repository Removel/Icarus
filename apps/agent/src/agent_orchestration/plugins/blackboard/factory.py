from apps.agent.src.agent_orchestration.plugin_runtime import (
    PluginRegistration,
    ProvidedCapability,
)
from apps.agent.src.agent_orchestration.plugins.blackboard.plugin import (
    BlackboardPlugin,
)


def create_plugin(
    plugin_id, workspace_path, session_id, config, required_capabilities,
    logger,
):
    del workspace_path, session_id, required_capabilities
    del logger
    plugin = BlackboardPlugin(
        plugin_id,
        required_context_sources=set(
            config.get("required_context_sources", [])
        ),
        model_role=config.get("model_role", "thinking"),
        system_prompt=str(config.get("system_prompt", "")),
        tools=config.get("tools"),
        initial_messages=list(config.get("initial_messages", [])),
    )
    return PluginRegistration(
        plugin=plugin,
        capabilities=(
            ProvidedCapability("conversation", "1.0.0", plugin),
        ),
        state_provider=plugin,
    )
