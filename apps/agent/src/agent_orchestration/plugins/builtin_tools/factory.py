from apps.agent.src.agent_orchestration.plugin_runtime import PluginRegistration
from apps.agent.src.agent_orchestration.plugins.builtin_tools.plugin import (
    BuiltinToolsPlugin,
)
from apps.agent.src.agent_orchestration.tools.builtin import create_builtin_tools


def create_plugin(
    plugin_id, workspace_path, session_id, config, required_capabilities,
    logger,
):
    del workspace_path, session_id, required_capabilities
    del logger
    tools = create_builtin_tools(
        max_output_bytes=config.get("max_output_bytes", 16 * 1024 * 1024),
        default_read_lines=config.get("default_read_lines", 200),
        max_read_lines=config.get("max_read_lines", 2000),
    )
    return PluginRegistration(
        plugin=BuiltinToolsPlugin(plugin_id),
        tools=tuple(tools),
    )
