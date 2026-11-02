from icarus.runtime import PluginRegistration
from .plugin import ArgusPlugin


def create_plugin(plugin_id, workspace_path, session_id, config, required_capabilities, logger):
    return PluginRegistration(plugin=ArgusPlugin(plugin_id, config['bridge'], config['task_channels']), tools=())
