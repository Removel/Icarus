from datetime import UTC, datetime, timedelta

import pytest

from apps.agent.src.agent_orchestration.plugins.persistence import (
    ConversationHistoryCorruptError,
    ConversationStore,
    DataPathResolver,
    SessionIdentity,
)
from apps.agent.src.runtime_update import RuntimeUpdate


def make_update(identity, task_id, update_type, payload=None):
    return RuntimeUpdate(
        workspace_key=identity.workspace_key,
        session_id=identity.session_id,
        task_id=task_id,
        type=update_type,
        payload=payload or {},
        occurred_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def test_conversation_store追加并按sequence读取(tmp_path):
    resolver = DataPathResolver(tmp_path / "data")
    identity = SessionIdentity.create(tmp_path, "session")
    store = ConversationStore(resolver)

    first = store.append(identity, make_update(identity, "task", "user.message"))
    second = store.append(identity, make_update(identity, "task", "task.accepted"))
    records, cursor = store.read(identity, after_sequence=1)

    assert first.sequence == 1
    assert second.sequence == 2
    assert records == (second,)
    assert cursor == 2


def test_conversation_store旧session为空且截断尾部可恢复(tmp_path):
    resolver = DataPathResolver(tmp_path / "data")
    identity = SessionIdentity.create(tmp_path, "session")
    resolver.ensure_session(identity)
    store = ConversationStore(resolver)
    assert store.read(identity) == ((), 0)

    store.append(identity, make_update(identity, "task", "user.message"))
    path = resolver.conversation_file(identity)
    with path.open("ab") as handle:
        handle.write(b'{"broken"')
    records, cursor = store.read(identity)
    assert len(records) == 1
    assert cursor == 1
    assert path.read_bytes().endswith(b"\n")


def test_conversation_store拒绝中间损坏和sequence缺口(tmp_path):
    resolver = DataPathResolver(tmp_path / "data")
    identity = SessionIdentity.create(tmp_path, "session")
    resolver.ensure_session(identity)
    path = resolver.conversation_file(identity)
    path.write_text('{"broken"}\n{}\n', encoding="utf-8")

    with pytest.raises(ConversationHistoryCorruptError):
        ConversationStore(resolver).read(identity)


def test_conversation_store读取会话摘要且不修改截断尾部(tmp_path):
    resolver = DataPathResolver(tmp_path / "data")
    identity = SessionIdentity.create(tmp_path, "session")
    store = ConversationStore(resolver)
    first = make_update(
        identity,
        "task-1",
        "user.message",
        {"text": "  first\n  message  ", "resources": []},
    )
    store.append(identity, first)
    later = make_update(identity, "task-1", "assistant.text_delta")
    later = RuntimeUpdate(
        workspace_key=later.workspace_key,
        session_id=later.session_id,
        task_id=later.task_id,
        type=later.type,
        payload=later.payload,
        occurred_at=later.occurred_at + timedelta(minutes=1),
    )
    store.append(identity, later)
    path = resolver.conversation_file(identity)
    with path.open("ab") as handle:
        handle.write(b'{"broken"')
    before = path.read_bytes()

    summary = store.read_summary(identity)

    assert summary is not None
    assert summary.first_user_input == "first message"
    assert summary.last_public_activity_at == later.occurred_at
    assert path.read_bytes() == before


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"text": "", "resources": [{"resource_id": "image"}]}, "[Image]"),
        ({"text": "", "resources": []}, "[Message]"),
        ({"text": "x" * 300, "resources": []}, "x" * 255 + "…"),
    ],
)
def test_conversation_store摘要回退和长度限制(tmp_path, payload, expected):
    resolver = DataPathResolver(tmp_path / "data")
    identity = SessionIdentity.create(tmp_path, "session")
    store = ConversationStore(resolver)
    store.append(identity, make_update(identity, "task", "user.message", payload))

    summary = store.read_summary(identity)

    assert summary is not None
    assert summary.first_user_input == expected
