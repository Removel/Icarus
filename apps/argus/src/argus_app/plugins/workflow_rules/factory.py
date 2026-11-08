from icarus.runtime import PluginRegistration, ProvidedCapability
from argus_app.plugins.workflow_rules.plugin import WorkflowRules


def create_plugin(plugin_id, workspace_path, session_id, config, required_capabilities, logger):
    del workspace_path, session_id, required_capabilities, logger
    plugin = WorkflowRules(plugin_id, config['store'], config['engine'], config['rule_files'],
                           discovery_enabled=config.get('discovery_enabled', False),
                           discovery_rule_path=config.get('discovery_rule_path'),
                           discovery_fallback=config.get('discovery_fallback'))
    plugin.safety_review = config.get('safety_review')
    plugin.channel_namer = config.get('channel_namer')
    plugin.quiet_seconds = config.get('quiet_seconds', 5.0)
    plugin.max_wait_seconds = config.get('max_wait_seconds', 30.0)
    return PluginRegistration(plugin=plugin, capabilities=tuple(
        ProvidedCapability(contract.capability_id, contract.version, plugin)
        for contract in plugin.provided_capabilities))
