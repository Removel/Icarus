"""Factory for the built-in MCP Client Plugin."""

from apps.agent.src.agent_orchestration.plugin_runtime import PluginRegistration
from apps.agent.src.agent_orchestration.plugins.mcp.client_manager import (
    MCPClientManager,
)
from apps.agent.src.agent_orchestration.plugins.mcp.config import parse_mcp_servers
from apps.agent.src.agent_orchestration.plugins.mcp.plugin import MCPPlugin
from apps.agent.src.agent_orchestration.plugins.mcp.result_converter import (
    MCPResultConverter,
)
from apps.agent.src.agent_orchestration.plugins.mcp.tools import create_mcp_tools
from apps.agent.src.agent_orchestration.plugins.persistence.runtime import (
    PersistenceRuntime,
    PersistenceSession,
)
from apps.agent.src.agent_orchestration.plugins.persistence.redactor import Redactor
from apps.agent.src.agent_orchestration.plugins.persistence.session_identity import (
    SessionIdentity,
)


def create_plugin(
    plugin_id, workspace_path, session_id, config, required_capabilities, logger
):
    del session_id
    persistence_runtime = required_capabilities[("persistence", "runtime")]
    identity = required_capabilities[("persistence", "session")]
    redactor = required_capabilities[("persistence", "redactor")]
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
    return PluginRegistration(
        plugin=plugin,
        tools=create_mcp_tools(plugin),
    )
