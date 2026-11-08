from datetime import UTC, datetime

import pytest

from agent_app.application.services import history_projector
from icarus.core.identity import SessionIdentity
from icarus.core.runtime_update import RuntimeUpdate


def records(identity, rows):
    return tuple(RuntimeUpdate(
        workspace_key=identity.workspace_key, session_id=identity.session_id,
        task_id=task, type=kind, payload=payload, occurred_at=datetime.now(UTC), sequence=index,
    ) for index, (task, kind, payload) in enumerate(rows, 1))


def test_snapshot_preserves_early_correction_and_only_completed_final_context(tmp_path):
    assert hasattr(history_projector, "persona_snapshot"), "missing persisted persona snapshot projection"
    identity = SessionIdentity.create(tmp_path, "source")
    rows = records(identity, [
        ("t1", "user.message", {"text": "以后请更简洁"}),
        ("t1", "assistant.message", {"text": "INTERMEDIATE"}),
        ("t1", "user.correction", {"text": "不是只针对这次"}),
        ("t1", "assistant.thinking", {"text": "PRIVATE"}),
        ("t1", "tool.completed", {"output_preview": "PRIVATE"}),
        ("t1", "assistant.message", {"text": "我会保持简洁"}),
        ("t1", "task.finished", {"status": "completed"}),
        ("t2", "user.message", {"text": "语气可以温和些"}),
        ("t2", "assistant.message", {"text": "FAILED-ANSWER"}),
        ("t2", "task.finished", {"status": "failed"}),
    ])
    snapshot = history_projector.persona_snapshot(identity, "default", rows, 0, 10)
    assert [message.text for message in snapshot.messages] == [
        "以后请更简洁", "不是只针对这次", "我会保持简洁", "语气可以温和些",
    ]
    assert [message.sequence for message in snapshot.messages] == [1, 3, 6, 8]
    assert snapshot == history_projector.persona_snapshot(identity, "default", rows, 0, 10)
    incremental = history_projector.persona_snapshot(identity, "default", rows, 7, 10)
    assert [message.text for message in incremental.messages] == ["语气可以温和些"]
    assert incremental.cycle_id != snapshot.cycle_id


def test_snapshot_rejects_unfinished_history_and_excludes_later_messages(tmp_path):
    assert hasattr(history_projector, "persona_snapshot"), "missing persisted persona snapshot projection"
    identity = SessionIdentity.create(tmp_path, "source")
    rows = records(identity, [
        ("t1", "user.message", {"text": "first"}),
        ("t1", "task.finished", {"status": "completed"}),
        ("t2", "user.message", {"text": "later"}),
    ])
    snapshot = history_projector.persona_snapshot(identity, "default", rows, 0, 2)
    assert [message.text for message in snapshot.messages] == ["first"]
    with pytest.raises(ValueError, match="history_busy"):
        history_projector.persona_snapshot(identity, "default", rows, 0, 3)


@pytest.mark.parametrize("final_has_tools", [False, True])
def test_snapshot_respects_optional_tool_marker_without_promoting_earlier_steps(tmp_path, final_has_tools):
    identity = SessionIdentity.create(tmp_path, "source")
    rows = records(identity, [
        ("t1", "user.message", {"text": "请长期保持简洁"}),
        ("t1", "assistant.message", {"text": "EARLIER-STEP", "has_tool_calls": False}),
        ("t1", "assistant.message", {"text": "LAST-STEP", "has_tool_calls": final_has_tools}),
        ("t1", "task.finished", {"status": "completed"}),
    ])
    snapshot = history_projector.persona_snapshot(identity, "default", rows, 0, 4)
    assert [item.text for item in snapshot.messages] == (
        ["请长期保持简洁"] if final_has_tools else ["请长期保持简洁", "LAST-STEP"]
    )
