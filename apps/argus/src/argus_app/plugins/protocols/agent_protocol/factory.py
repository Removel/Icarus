from icarus.runtime import PluginRegistration, ProvidedCapability
from argus_app.plugins.protocols.agent_protocol.plugin import AgentProtocol


def create_plugin(plugin_id, workspace_path, session_id, config, required_capabilities, logger):
    del workspace_path, session_id, required_capabilities, logger
    plugin = AgentProtocol(plugin_id, config['store'])
    return PluginRegistration(plugin=plugin, capabilities=tuple(
        ProvidedCapability(contract.capability_id, contract.version, plugin)
        for contract in plugin.provided_capabilities))
