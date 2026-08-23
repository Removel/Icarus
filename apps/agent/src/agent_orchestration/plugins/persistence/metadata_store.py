"""Workspace 和 Session 元数据。"""

from datetime import UTC, datetime
import json
from pathlib import Path
from uuid import uuid4

from apps.agent.src.agent_orchestration.plugins.persistence.path_resolver import (
    DataPathResolver,
)
from apps.agent.src.agent_orchestration.plugins.persistence.session_identity import (
    SessionIdentity,
)


class MetadataStore:
    def __init__(self, resolver: DataPathResolver) -> None:
        self.resolver = resolver

    def initialize(self, identity: SessionIdentity) -> None:
        self.resolver.ensure_session(identity)
        now = datetime.now(UTC).isoformat()
        workspace_path = self.resolver.workspace_metadata(identity)
        workspace = self._read_json(workspace_path) or {
            "workspace_key": identity.workspace_key,
            "workspace_path": str(identity.workspace_path),
            "created_at": now,
        }
        workspace["workspace_path"] = str(identity.workspace_path)
        workspace["last_seen_at"] = now
        self._write_json(workspace_path, workspace)

        session_path = self.resolver.session_metadata(identity)
        session = self._read_json(session_path) or {
            "session_id": identity.session_id,
            "created_at": now,
        }
        session["updated_at"] = now
        session["status"] = "active"
        self._write_json(session_path, session)

    def update_session_status(
        self,
        identity: SessionIdentity,
        status: str,
    ) -> None:
        path = self.resolver.session_metadata(identity)
        session = self._read_json(path)
        if session is None:
            raise FileNotFoundError(path)
        session["updated_at"] = datetime.now(UTC).isoformat()
        session["status"] = status
        self._write_json(path, session)

    def read_json(self, path: Path) -> dict | None:
        return self._read_json(path)

    def write_json(self, path: Path, data: dict) -> None:
        self._write_json(path, data)

    @staticmethod
    def _read_json(path: Path) -> dict | None:
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def _write_json(path: Path, data: dict) -> None:
        temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        temporary.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        try:
            temporary.chmod(0o600)
        except OSError:
            pass
        temporary.replace(path)
        try:
            path.chmod(0o600)
        except OSError:
            pass
