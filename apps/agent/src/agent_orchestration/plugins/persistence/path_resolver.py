"""持久化文件路径解析。"""

from pathlib import Path
import re

from apps.agent.src.agent_orchestration.plugins.persistence.session_identity import (
    SessionIdentity,
)


_SAFE_ID = re.compile(r"^[A-Za-z0-9._-]+$")


class DataPathResolver:
    def __init__(self, data_dir: str | Path) -> None:
        resolved = Path(data_dir).expanduser()
        if not resolved.is_absolute():
            raise ValueError("ICARUS_DATA_DIR must be an absolute path")
        self.data_dir = resolved.resolve()

    @property
    def workspaces_dir(self) -> Path:
        return self.data_dir / "workspaces"

    @property
    def global_skills_dir(self) -> Path:
        return self.data_dir / "skills"

    @property
    def incoming_dir(self) -> Path:
        return self.data_dir / "incoming"

    def workspace_dir(self, identity: SessionIdentity) -> Path:
        self._validate_id(identity.workspace_key, "workspace_key")
        return self.workspaces_dir / identity.workspace_key

    def workspace_metadata(self, identity: SessionIdentity) -> Path:
        return self.workspace_dir(identity) / "workspace.json"

    def workspace_log(self, identity: SessionIdentity) -> Path:
        return self.workspace_dir(identity) / "runtime.log"

    def sessions_dir(self, identity: SessionIdentity) -> Path:
        return self.workspace_dir(identity) / "sessions"

    def session_dir(self, identity: SessionIdentity) -> Path:
        self._validate_id(identity.session_id, "session_id")
        return self.sessions_dir(identity) / identity.session_id

    def session_metadata(self, identity: SessionIdentity) -> Path:
        return self.session_dir(identity) / "session.json"

    def trace_file(self, identity: SessionIdentity) -> Path:
        return self.session_dir(identity) / "trace.jsonl"

    def session_log(self, identity: SessionIdentity) -> Path:
        return self.session_dir(identity) / "runtime.log"

    def assets_dir(self, identity: SessionIdentity) -> Path:
        return self.session_dir(identity) / "assets"

    def ensure_workspace(self, identity: SessionIdentity) -> Path:
        directory = self.workspace_dir(identity)
        self._mkdir(directory)
        return directory

    def ensure_session(self, identity: SessionIdentity) -> Path:
        self.ensure_workspace(identity)
        session_directory = self.session_dir(identity)
        self._mkdir(session_directory)
        self._mkdir(self.assets_dir(identity))
        return session_directory

    def session_exists(self, identity: SessionIdentity) -> bool:
        return self.session_dir(identity).is_dir()

    def list_session_ids(self, identity: SessionIdentity) -> tuple[str, ...]:
        directory = self.sessions_dir(identity)
        if not directory.is_dir():
            return ()
        return tuple(
            child.name
            for child in sorted(directory.iterdir(), key=lambda item: item.name)
            if child.is_dir() and _SAFE_ID.fullmatch(child.name)
        )

    @staticmethod
    def _validate_id(value: str, name: str) -> None:
        if not value or not _SAFE_ID.fullmatch(value):
            raise ValueError(f"{name} contains unsafe characters")

    @staticmethod
    def _mkdir(path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            path.chmod(0o700)
        except OSError:
            pass
