"""A business session association, independent of database sessions."""
from dataclasses import dataclass
from .records import SessionLinkRow


@dataclass(frozen=True)
class SessionLink:
    platform: str
    account_id: str
    subject: str
    workspace_path: str
    workspace_key: str
    session_id: str

    def to_row(self, *, id, created_at):
        return SessionLinkRow(id=id, platform=self.platform, account_id=self.account_id,
            subject=self.subject, workspace_path=self.workspace_path, workspace_key=self.workspace_key,
            session_id=self.session_id, created_at=created_at)

    @classmethod
    def from_row(cls, row):
        return cls(row.platform, row.account_id, row.subject, row.workspace_path, row.workspace_key, row.session_id)
