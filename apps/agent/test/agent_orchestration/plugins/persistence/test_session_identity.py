from pathlib import Path

from apps.agent.src.agent_orchestration.plugins.persistence import SessionIdentity


def test_session_identity_同一路径稳定且默认session不同(tmp_path):
    first = SessionIdentity.create(tmp_path)
    second = SessionIdentity.create(Path(tmp_path) / ".")

    assert first.workspace_key == second.workspace_key
    assert first.workspace_path == tmp_path.resolve()
    assert first.session_id != second.session_id


def test_session_identity_保留显式session和correlation(tmp_path):
    identity = SessionIdentity.create(
        tmp_path,
        session_id="session-1",
        correlation_id="task-1",
    )

    assert identity.session_id == "session-1"
    assert identity.correlation_id == "task-1"
