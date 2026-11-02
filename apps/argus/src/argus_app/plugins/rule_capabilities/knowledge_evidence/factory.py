from icarus.runtime import PluginRegistration, ProvidedCapability
from argus_app.plugins.rule_capabilities.knowledge_evidence.plugin import KnowledgeEvidencePlugin


def create_plugin(plugin_id, workspace_path, session_id, config, required_capabilities, logger):
    del workspace_path, session_id, required_capabilities, logger
    plugin = KnowledgeEvidencePlugin(plugin_id, config.get('client'), kb=config.get('kb', ''))
    return PluginRegistration(plugin=plugin, capabilities=tuple(
        ProvidedCapability(contract.capability_id, contract.version, plugin)
        for contract in plugin.provided_capabilities))
