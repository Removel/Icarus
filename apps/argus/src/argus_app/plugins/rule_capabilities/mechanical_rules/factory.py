from icarus.runtime import PluginRegistration, ProvidedCapability
from argus_app.plugins.rule_capabilities.mechanical_rules.plugin import MechanicalRulesPlugin


def create_plugin(plugin_id, workspace_path, session_id, config, required_capabilities, logger):
    del workspace_path, session_id, required_capabilities, logger
    plugin = MechanicalRulesPlugin(plugin_id)
    return PluginRegistration(plugin=plugin, capabilities=tuple(
        ProvidedCapability(contract.capability_id, contract.version, plugin)
        for contract in plugin.provided_capabilities))
