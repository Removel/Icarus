import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from apps.agent.src.agent_orchestration.plugins.persistence import SessionIdentity
from apps.agent.src.application.session_store import (
    SessionAlreadyExistsError,
    SessionNotFoundError,
    SessionStore,
)
from apps.agent.src.application.runtime_status import SessionSummary
from apps.agent.src.runtime_update import RuntimeUpdate


def update(identity, update_type, *, at, task_id="task", payload=None):
    return RuntimeUpdate(
        workspace_key=identity.workspace_key,
        session_id=identity.session_id,
        task_id=task_id,
        type=update_type,
        payload=payload or {},
        occurred_at=at,
    )


def test_session_store管理session和conversation(tmp_path):
    async def run():
        store = SessionStore(tmp_path / "data")
        await store.start()
        first = SessionIdentity.create(tmp_path / "workspace", "first")
        second = SessionIdentity.create(tmp_path / "workspace", "second")
        await store.create_session(first)
        await store.create_session(second)
        now = datetime.now(UTC)
        first_record = await store.append_update(
            first,
            update(
                first,
                "user.message",
                at=now,
                payload={"text": "  hello\n world  ", "resources": []},
            ),
        )
        second_record = await store.append_update(
            second,
            update(
                second,
                "user.message",
                at=now + timedelta(seconds=1),
                payload={"text": "newer", "resources": []},
            ),
        )
        finish = await store.append_update(
            first,
            update(first, "task.finished", at=now + timedelta(seconds=2)),
        )
        summaries = await store.list_session_summaries(first.workspace_key)
        records, cursor = await store.read_updates(first, after_sequence=1)
        await store.close()
        return first_record, second_record, finish, summaries, records, cursor

    first_record, second_record, finish, summaries, records, cursor = (
        asyncio.run(run())
    )
    assert first_record.sequence == 1
    assert second_record.sequence == 1
    assert finish.sequence == 2
    assert [item.session_id for item in summaries] == ["first", "second"]
    assert [item.first_user_input for item in summaries] == [
        "hello world",
        "newer",
    ]
    assert records == (finish,)
    assert cursor == 2
    assert finish.occurred_at.tzinfo is UTC


def test_session_store软删除空session并保留记录(tmp_path):
    async def run():
        store = SessionStore(tmp_path / "data")
        await store.start()
        empty = SessionIdentity.create(tmp_path, "empty")
        non_empty = SessionIdentity.create(tmp_path, "non-empty")
        await store.create_session(empty)
        await store.create_session(non_empty)
        await store.append_update(
            non_empty,
            update(
                non_empty,
                "user.message",
                at=datetime.now(UTC),
                payload={"text": "kept", "resources": []},
            ),
        )
        results = (
            await store.soft_delete_empty_session(
                empty, reason="empty_cleanup"
            ),
            await store.soft_delete_empty_session(
                non_empty, reason="empty_cleanup"
            ),
            await store.soft_delete_empty_session(
                empty, reason="empty_cleanup"
            ),
        )
        active = await store.session_exists(empty)
        deleted = await store.get_session(empty, include_deleted=True)
        with pytest.raises(SessionNotFoundError):
            await store.read_updates(empty)
        await store.close()
        return results, active, deleted

    results, active, deleted = asyncio.run(run())
    assert results == ("discarded", "not_empty", "not_found")
    assert active is False
    assert deleted is not None
    assert deleted.delete_reason == "empty_cleanup"
    assert deleted.deleted_at is not None


def test_session_store拒绝重复和旧数据目录(tmp_path):
    async def duplicate():
        store = SessionStore(tmp_path / "new")
        await store.start()
        identity = SessionIdentity.create(tmp_path, "same")
        await store.create_session(identity)
        with pytest.raises(SessionAlreadyExistsError):
            await store.create_session(identity)
        await store.close()

    asyncio.run(duplicate())
    legacy = tmp_path / "legacy" / "workspaces" / "workspace" / "sessions" / "old"
    legacy.mkdir(parents=True)

    async def legacy_start():
        store = SessionStore(tmp_path / "legacy")
        with pytest.raises(RuntimeError, match="legacy Session data"):
            await store.start()

    asyncio.run(legacy_start())


def test_session_store重启后恢复并支持并发session写入(tmp_path):
    async def run():
        data_dir = tmp_path / "data"
        workspace = tmp_path / "workspace"
        first = SessionIdentity.create(workspace, "first")
        second = SessionIdentity.create(workspace, "second")
        store = SessionStore(data_dir)
        await store.start()
        await asyncio.gather(
            store.create_session(first),
            store.create_session(second),
        )
        now = datetime.now(UTC)
        recorded = await asyncio.gather(
            store.append_update(
                first,
                update(
                    first,
                    "user.message",
                    at=now,
                    payload={"text": "first", "resources": []},
                ),
            ),
            store.append_update(
                second,
                update(
                    second,
                    "user.message",
                    at=now,
                    payload={"text": "second", "resources": []},
                ),
            ),
        )
        await store.close()

        restored = SessionStore(data_dir)
        await restored.start()
        first_records, first_cursor = await restored.read_updates(first)
        summaries = await restored.list_session_summaries(first.workspace_key)
        await restored.close()
        return recorded, first_records, first_cursor, summaries

    recorded, first_records, first_cursor, summaries = asyncio.run(run())
    assert [item.sequence for item in recorded] == [1, 1]
    assert first_records == (recorded[0],)
    assert first_cursor == 1
    assert {item.session_id for item in summaries} == {"first", "second"}


def test_session_store并发追加同一session仍分配连续sequence(tmp_path):
    async def run():
        store = SessionStore(tmp_path / "data")
        await store.start()
        identity = SessionIdentity.create(tmp_path, "session")
        await store.create_session(identity)
        now = datetime.now(UTC)
        recorded = await asyncio.gather(
            *(
                store.append_update(
                    identity,
                    update(
                        identity,
                        "assistant.text_delta",
                        at=now + timedelta(microseconds=index),
                        payload={"step": 1, "text": str(index)},
                    ),
                )
                for index in range(10)
            )
        )
        records, cursor = await store.read_updates(identity)
        await store.close()
        return recorded, records, cursor

    recorded, records, cursor = asyncio.run(run())
    assert sorted(item.sequence for item in recorded) == list(range(1, 11))
    assert [item.sequence for item in records] == list(range(1, 11))
    assert cursor == 10


def test_session_store关闭后拒绝调用(tmp_path):
    async def run():
        store = SessionStore(tmp_path / "data")
        await store.start()
        await store.close()
        identity = SessionIdentity.create(tmp_path, "session")
        with pytest.raises(RuntimeError, match="not running"):
            await store.session_exists(identity)
        with pytest.raises(RuntimeError, match="cannot be restarted"):
            await store.start()

    asyncio.run(run())


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"text": "", "resources": [{"resource_id": "image"}]}, "[Image]"),
        ({"text": "", "resources": []}, "[Message]"),
        ({"text": "x" * 300, "resources": []}, "x" * 255 + "…"),
    ],
)
def test_session_store摘要回退和长度限制(tmp_path, payload, expected):
    async def run():
        store = SessionStore(tmp_path / "data")
        await store.start()
        identity = SessionIdentity.create(tmp_path, "session")
        await store.create_session(identity)
        await store.append_update(
            identity,
            update(
                identity,
                "user.message",
                at=datetime.now(UTC),
                payload=payload,
            ),
        )
        summaries = await store.list_session_summaries(identity.workspace_key)
        await store.close()
        return summaries

    summaries = asyncio.run(run())
    assert summaries == (SessionSummary("session", expected),)
