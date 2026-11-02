from icarus.runtime import PluginRegistration, ProvidedCapability
from argus_app.plugins.protocols.feishu_protocol.plugin import FeishuProtocol


def create_plugin(plugin_id, workspace_path, session_id, config, required_capabilities, logger):
    del workspace_path, session_id, required_capabilities, logger
    plugin = FeishuProtocol(plugin_id, config['store'])
    return PluginRegistration(plugin=plugin, capabilities=tuple(
        ProvidedCapability(contract.capability_id, contract.version, plugin)
        for contract in plugin.provided_capabilities))
