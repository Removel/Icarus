from icarus.runtime import PluginRegistration, ProvidedCapability
from argus_app.plugins.rule_capabilities.semantic_judgement.plugin import SemanticJudgementPlugin


def create_plugin(plugin_id, workspace_path, session_id, config, required_capabilities, logger):
    del workspace_path, session_id, required_capabilities, logger
    plugin = SemanticJudgementPlugin(plugin_id, config.get('semantic'))
    return PluginRegistration(plugin=plugin, capabilities=tuple(
        ProvidedCapability(contract.capability_id, contract.version, plugin)
        for contract in plugin.provided_capabilities))
