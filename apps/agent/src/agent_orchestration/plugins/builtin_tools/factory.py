from apps.agent.src.agent_orchestration.plugin_runtime import PluginRegistration
from apps.agent.src.agent_orchestration.plugins.builtin_tools.plugin import (
    BuiltinToolsPlugin,
)
from apps.agent.src.agent_orchestration.tools.builtin import create_builtin_tools


def create_plugin(
    plugin_id, workspace_path, session_id, config, required_capabilities,
    logger,
):
    del workspace_path, session_id, config, required_capabilities
    del logger
    return PluginRegistration(
        plugin=BuiltinToolsPlugin(plugin_id),
        tools=tuple(create_builtin_tools()),
    )
