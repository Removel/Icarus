"""Only explicitly bound background sessions can construct this optional plugin."""

from icarus.core.registration import PluginRegistration
from icarus.core.session import SessionContext

from .plugin import ArgusPlugin
from .config import PluginConfig


def create_plugin(plugin_id, workspace_path, session_id, config, required_capabilities, logger):
    PluginConfig.model_validate(config.get('_user_config', {}))
    context = config.get('session_context', SessionContext())
    if context.kind != 'argus_background':
        raise ValueError('Argus is not active for this session')
    if not context.metadata.get('binding'):
        raise ValueError('Argus background session requires a binding')
    client, append_record = config.get('client'), config.get('append_record')
    if client is None or append_record is None:
        raise ValueError('Argus background facilities are required')
    return PluginRegistration(plugin=ArgusPlugin(plugin_id, context=context, client=client,
        append_record=append_record, prompt_fits=config.get('prompt_fits')))
