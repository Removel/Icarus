from icarus.runtime import PluginRegistration
from agent_app.plugins.builtin_tools.plugin import (
    BuiltinToolsPlugin,
)
from agent_app.plugins.builtin_tools.tools import create_builtin_tools


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
    plugin = BuiltinToolsPlugin(plugin_id)
    plugin._tools = tuple(tools)
    return PluginRegistration(plugin=plugin)
