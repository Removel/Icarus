"""Plugin-owned state and recording backends; assets are injected infrastructure."""

from pathlib import Path
import logging

from icarus.core.identity import SessionIdentity
from icarus.core.registration import PluginRegistration, ProvidedCapability
from icarus.runtime.assets import PersistenceRuntime
from agent_app.plugins.persistence.plugin import PersistencePlugin
from agent_app.plugins.persistence.json_state_store import JsonStateStore
from agent_app.plugins.persistence.log_handler import WorkspaceSessionFileHandler
from agent_app.plugins.persistence.trace_writer import FileTraceWriter
from agent_app.plugins.persistence.recording import FileRecordingBackend
from agent_app.utils.redactor import Redactor


def create_plugin(plugin_id, workspace_path, session_id, config, required_capabilities, logger):
    runtime = config["runtime"]
    identity = config["identity"]
    if not isinstance(runtime, PersistenceRuntime) or not isinstance(identity, SessionIdentity):
        raise TypeError("persistence requires injected assets and identity")
    runtime_logger = config.get("runtime_logger", logger)
    if not isinstance(runtime_logger, logging.Logger):
        raise TypeError("persistence requires a logger")
    backend = FileRecordingBackend(FileTraceWriter(runtime.resolver), Redactor())
    plugin = PersistencePlugin(plugin_id, runtime.resolver, identity, runtime_logger,
                               JsonStateStore(), WorkspaceSessionFileHandler(runtime.resolver, identity))
    return PluginRegistration(
        plugin=plugin,
        capabilities=(ProvidedCapability("state.store", "1.0.0", plugin),),
        backends=(backend,),
    )
