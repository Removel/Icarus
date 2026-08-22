"""Workspace 和 Trace Session 身份。"""

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from uuid import uuid4


@dataclass(frozen=True)
class SessionIdentity:
    workspace_path: Path
    workspace_key: str
    session_id: str

    @classmethod
    def create(
        cls,
        workspace_path: str | Path,
        session_id: str | None = None,
    ) -> "SessionIdentity":
        normalized_path = Path(workspace_path).expanduser().resolve()
        workspace_key = sha256(
            str(normalized_path).encode("utf-8")
        ).hexdigest()[:16]
        return cls(
            workspace_path=normalized_path,
            workspace_key=workspace_key,
            session_id=session_id or uuid4().hex,
        )
