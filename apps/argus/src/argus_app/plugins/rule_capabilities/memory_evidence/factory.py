from icarus.runtime import PluginRegistration, ProvidedCapability
from argus_app.plugins.rule_capabilities.memory_evidence.plugin import MemoryEvidencePlugin


def create_plugin(plugin_id, workspace_path, session_id, config, required_capabilities, logger):
    del workspace_path, session_id, required_capabilities, logger
    plugin = MemoryEvidencePlugin(plugin_id, config.get('client'), user_id=config.get('user_id', ''),
        agent_id=config.get('agent_id', ''), workspace_key=config.get('workspace_key', ''))
    return PluginRegistration(plugin=plugin, capabilities=tuple(
        ProvidedCapability(contract.capability_id, contract.version, plugin)
        for contract in plugin.provided_capabilities))
