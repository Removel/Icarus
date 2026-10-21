"""Construct persona using explicit application facilities, without network I/O."""

from agent_app.plugins.persona.config import PersonaConfig
from agent_app.plugins.persona.plugin import PersonaPlugin
from icarus.core.registration import PluginRegistration


def create_plugin(plugin_id, workspace_path, session_id, config, required_capabilities, logger):
    settings = PersonaConfig.model_validate(config.get("_user_config", {}))
    data_dir = config["config_model"].icarus_data_dir
    if data_dir is None:
        raise ValueError("ICARUS_DATA_DIR is required for persona")
    return PluginRegistration(plugin=PersonaPlugin(plugin_id, config=settings,
        data_dir=data_dir, session_id=session_id, logger=logger, task_channels=config.get("task_channels")))
