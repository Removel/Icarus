"""Compatibility composition for file state, trace and log backends."""

import logging

from icarus.runtime.assets import PersistenceRuntime as AssetRuntime, PersistenceSession, ImageAssetError
from icarus.core.identity import SessionIdentity
from agent_app.utils.redactor import Redactor
from agent_app.plugins.persistence.json_state_store import JsonStateStore
from agent_app.plugins.persistence.log_handler import WorkspaceSessionFileHandler
from agent_app.plugins.persistence.trace_writer import FileTraceWriter


class PersistenceRuntime(AssetRuntime):
    def __init__(self, data_dir, workspace_path, *, flush_every=1, warning_file_size_bytes=None):
        super().__init__(data_dir, workspace_path)
        self.state_store = JsonStateStore()
        self.redactor = Redactor()
        self.trace_writer = FileTraceWriter(self.resolver, flush_every=flush_every,
            warning_file_size_bytes=warning_file_size_bytes)
        self.log_handler = WorkspaceSessionFileHandler(self.resolver, self.workspace_identity)
        self._hook_registry = None
        self._logger = None

    def start(self, hook_registry=None, logger=None, session_identity=None):
        if self.is_running:
            return
        super().start()
        self.trace_writer.start()
        self._logger = logger or logging.getLogger()
        if session_identity is not None:
            self.log_handler.bind_session(session_identity)
        self._logger.addHandler(self.log_handler)

    def stop(self, drain=True, logger=None):
        if not self.is_running:
            return
        (logger or self._logger or logging.getLogger()).removeHandler(self.log_handler)
        self.trace_writer.stop(drain=drain)
        self.log_handler.close()
        self._logger = None
        super().stop()
