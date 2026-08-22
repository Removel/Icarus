"""按 Workspace/Session 路由的运行日志 Handler。"""

from datetime import UTC, datetime
import logging
from pathlib import Path
import sys
import threading

from apps.agent.src.agent_orchestration.hooks.hook_context import get_hook_context
from apps.agent.src.agent_orchestration.plugins.persistence.path_resolver import (
    DataPathResolver,
)
from apps.agent.src.agent_orchestration.plugins.persistence.session_identity import (
    SessionIdentity,
)


class WorkspaceSessionFileHandler(logging.Handler):
    def __init__(
        self,
        resolver: DataPathResolver,
        workspace_identity: SessionIdentity,
    ) -> None:
        super().__init__()
        self.resolver = resolver
        self.workspace_identity = workspace_identity
        self._handles: dict[Path, object] = {}
        self._lock = threading.RLock()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            identity = self._identity_from_context()
            path = (
                self.resolver.session_log(identity)
                if identity is not None
                else self.resolver.workspace_log(self.workspace_identity)
            )
            self._write(path, record, identity)
        except Exception:
            self.handleError(record)

    def close(self) -> None:
        with self._lock:
            for handle in self._handles.values():
                try:
                    handle.flush()
                    handle.close()
                except OSError:
                    pass
            self._handles.clear()
        super().close()

    def handleError(self, record: logging.LogRecord) -> None:
        try:
            print(
                f"Icarus runtime log write failed: {record.getMessage()}",
                file=sys.stderr,
            )
        except Exception:
            pass

    def _identity_from_context(self) -> SessionIdentity | None:
        context = get_hook_context()
        if context is None:
            return None
        workspace_path = context.data.get("workspace_path")
        workspace_key = context.data.get("workspace_key")
        session_id = context.data.get("session_id")
        if not workspace_path or not workspace_key or not session_id:
            return None
        return SessionIdentity(
            workspace_path=Path(str(workspace_path)),
            workspace_key=str(workspace_key),
            session_id=str(session_id),
        )

    def _write(
        self,
        path: Path,
        record: logging.LogRecord,
        identity: SessionIdentity | None,
    ) -> None:
        if identity is None:
            self.resolver.ensure_workspace(self.workspace_identity)
        else:
            self.resolver.ensure_session(identity)
        with self._lock:
            handle = self._handles.get(path)
            if handle is None:
                handle = path.open("a", encoding="utf-8")
                try:
                    path.chmod(0o600)
                except OSError:
                    pass
                self._handles[path] = handle
            context = get_hook_context()
            context_data = context.data if context else {}
            timestamp = datetime.fromtimestamp(record.created, UTC).isoformat()
            fields = [
                timestamp,
                record.levelname,
                record.name,
                f"session={identity.session_id if identity else '-'}",
                f"task={context_data.get('task_id', '-')}",
                f"run={context.run_id if context else '-'}",
                f"plugin={context_data.get('plugin_id', '-')}",
                record.getMessage(),
            ]
            if record.exc_info:
                fields.append(self.formatException(record.exc_info))
            handle.write(" ".join(fields) + "\n")
            handle.flush()

    @staticmethod
    def formatException(exc_info) -> str:
        return logging.Formatter().formatException(exc_info)
