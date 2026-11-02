from icarus.runtime import PluginRegistration, ProvidedCapability
from argus_app.plugins.icarus_bridge.plugin import BridgePlugin


def create_plugin(plugin_id, workspace_path, session_id, config, required_capabilities, logger):
    del workspace_path, session_id, required_capabilities, logger
    plugin = BridgePlugin(plugin_id, config['store'], config.get('bridge_client'), config['argus_url'],
                          rule_files=config.get('rule_files'), guide=config.get('guide'))
    return PluginRegistration(plugin=plugin, capabilities=tuple(
        ProvidedCapability(contract.capability_id, contract.version, plugin)
        for contract in plugin.provided_capabilities))
