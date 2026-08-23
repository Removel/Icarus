from apps.agent.src.agent_orchestration.agent_factory import AgentFactory
from apps.agent.src.agent_orchestration.hooks import HookDispatcher
from apps.agent.src.agent_orchestration.plugin_runtime import (
    PluginRegistration,
    ProvidedCapability,
)
from apps.agent.src.agent_orchestration.plugins.agent.plugin import AgentPlugin
from apps.agent.src.agent_orchestration.run_control import TaskChannelRegistry
from apps.agent.src.agent_orchestration.tools import ToolRegistry
from apps.agent.src.model_config import ConfigModel


def create_plugin(
    plugin_id, workspace_path, session_id, config, required_capabilities,
    logger,
):
    del workspace_path, session_id, required_capabilities, logger
    hook_registry = config.get("hook_registry")
    if hook_registry is None:
        raise ValueError("agent config requires hook_registry")
    config_model = config.get("config_model")
    if not isinstance(config_model, ConfigModel):
        raise ValueError("agent config requires config_model")
    tool_registry = config.get("tool_registry")
    if not isinstance(tool_registry, ToolRegistry):
        raise ValueError("agent config requires tool_registry")
    agent_factory = AgentFactory(
        config=config_model,
        tool_registry=tool_registry,
        hook_registry=hook_registry,
        register_builtin_tools=False,
    )
    task_channels = config.get("task_channels") or TaskChannelRegistry()
    try:
        plugin = AgentPlugin(
            plugin_id,
            agent_factory,
            task_channels=task_channels,
            hook_dispatcher=HookDispatcher(hook_registry),
        )
    except BaseException:
        agent_factory.close()
        raise
    return PluginRegistration(
        plugin=plugin,
        capabilities=(
            ProvidedCapability("task_control", "1.0.0", plugin),
            ProvidedCapability("task_channels", "1.0.0", task_channels),
        ),
    )
