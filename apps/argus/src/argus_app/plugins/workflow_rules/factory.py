from icarus.runtime import PluginRegistration, ProvidedCapability
from argus_app.plugins.workflow_rules.plugin import WorkflowRules


def create_plugin(plugin_id, workspace_path, session_id, config, required_capabilities, logger):
    del workspace_path, session_id, required_capabilities, logger
    plugin = WorkflowRules(plugin_id, config['store'], config['engine'], config['rule_files'],
                           discovery_enabled=config.get('discovery_enabled', False),
                           discovery_rule_path=config.get('discovery_rule_path'))
    plugin.safety_review = config.get('safety_review')
    return PluginRegistration(plugin=plugin, capabilities=tuple(
        ProvidedCapability(contract.capability_id, contract.version, plugin)
        for contract in plugin.provided_capabilities))
