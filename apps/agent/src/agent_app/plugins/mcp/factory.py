"""Factory for the built-in MCP Client Plugin."""

from icarus.runtime import PluginRegistration
from agent_app.plugins.mcp.client_manager import (
    MCPClientManager,
)
from agent_app.plugins.mcp.config import parse_mcp_servers
from agent_app.plugins.mcp.plugin import MCPPlugin
from agent_app.plugins.mcp.result_converter import (
    MCPResultConverter,
)
from agent_app.plugins.mcp.tools import create_mcp_tools
from icarus.runtime.assets import (
    PersistenceRuntime,
    PersistenceSession,
)
from agent_app.utils.redactor import Redactor
from icarus.core.identity import (
    SessionIdentity,
)


def create_plugin(
    plugin_id, workspace_path, session_id, config, required_capabilities, logger
):
    del session_id
    persistence_runtime = config["runtime"]
    identity = config["identity"]
    redactor = Redactor()
    if not isinstance(persistence_runtime, PersistenceRuntime):
        raise ValueError("mcp requires persistence runtime")
    if not isinstance(identity, SessionIdentity):
        raise ValueError("mcp requires persistence session")
    if not isinstance(redactor, Redactor):
        raise ValueError("mcp requires persistence redactor")
    servers = parse_mcp_servers(config.get("servers", {}))
    manager = config.get("client_manager") or MCPClientManager(
        servers, workspace_path=str(workspace_path), logger=logger, redactor=redactor
    )
    plugin = MCPPlugin(
        plugin_id,
        manager=manager,
        result_converter=MCPResultConverter(
            PersistenceSession(persistence_runtime, identity),
            redactor,
        ),
        redactor=redactor,
    )
    plugin._tools = tuple(create_mcp_tools(
            plugin,
            default_page_size=config.get("default_page_size", 50),
            max_page_size=config.get("max_page_size", 200),
        ))
    return PluginRegistration(
        plugin=plugin,
        tools=(),
    )
