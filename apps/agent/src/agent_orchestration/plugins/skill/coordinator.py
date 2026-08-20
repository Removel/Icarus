"""Process-local coordination for background Workspace maintenance."""

from threading import Lock
from uuid import uuid4


class WorkspaceMaintenanceCoordinator:
    """Provide non-blocking, thread-safe Workspace claims.

    The coordinator deliberately stores only Workspace keys. Async tasks remain
    owned by the Plugin and by the event loop that created them.
    """

    def __init__(self) -> None:
        self._lock = Lock()
        self._claims: dict[str, str] = {}

    def claim(self, workspace_key: str) -> str | None:
        """Return an ownership token, or ``None`` when Workspace is busy."""

        workspace_key = self._normalize_workspace_key(workspace_key)
        with self._lock:
            if workspace_key in self._claims:
                return None
            token = uuid4().hex
            self._claims[workspace_key] = token
            return token

    def release(self, workspace_key: str, token: str | None = None) -> bool:
        """Release only when *token* still owns the Workspace claim."""

        workspace_key = self._normalize_workspace_key(workspace_key)
        with self._lock:
            current = self._claims.get(workspace_key)
            if current is None:
                return False
            if token is not None and token != current:
                return False
            del self._claims[workspace_key]
            return True

    def is_claimed(self, workspace_key: str) -> bool:
        """Return whether the Workspace currently has an active claim."""

        workspace_key = self._normalize_workspace_key(workspace_key)
        with self._lock:
            return workspace_key in self._claims

    @property
    def active_workspace_keys(self) -> frozenset[str]:
        """Return a stable snapshot of all current claims."""

        with self._lock:
            return frozenset(self._claims)

    @staticmethod
    def _normalize_workspace_key(workspace_key: str) -> str:
        if not isinstance(workspace_key, str):
            raise ValueError("workspace_key cannot be empty")
        normalized = workspace_key.strip()
        if not normalized:
            raise ValueError("workspace_key cannot be empty")
        return normalized


PROCESS_WORKSPACE_MAINTENANCE_COORDINATOR = WorkspaceMaintenanceCoordinator()
