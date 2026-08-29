import asyncio
from pathlib import Path

import pytest

from apps.tui.src.replay import (
    ReplayFormatError,
    ReplayRuntimeService,
    decode_replay_record,
    load_replay,
)


FIXTURE = (
    Path(__file__).parent / "fixtures" / "synthetic_tui_events.jsonl"
)


def test_load_replay按终态切分三轮并保留unrelated_update():
    scenario = load_replay(FIXTURE)
    assert scenario.task_ids == ("task-1", "task-2", "task-3")
    ids = [update.task_id for update in scenario.turns[0].updates]
    assert ids[0] == "task-1"
    assert "unrelated-task" in ids
    assert ids[-1] == "task-1"


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"schema_version": 99}, "unsupported schema_version"),
        ({"payload": object()}, "invalid RuntimeUpdate"),
    ],
)
def test_decode_replay_record严格拒绝非法公共update(change, message):
    record = {
        "schema_version": 4,
        "workspace_key": "workspace",
        "session_id": "session",
        "task_id": "task",
        "type": "task.started",
        "payload": {},
        "occurred_at": "2026-01-01T00:00:00Z",
    }
    record.update(change)
    with pytest.raises(ReplayFormatError, match=message):
        decode_replay_record(record)


def test_decode_replay_record支持cancelled和compact():
    cancelled = decode_replay_record(
        {
            "schema_version": 4,
            "workspace_key": "workspace",
            "session_id": "session",
            "task_id": "task",
            "type": "task.finished",
            "payload": {"status": "cancelled"},
            "occurred_at": "2026-01-01T00:00:00Z",
        }
    )
    compact = decode_replay_record(
        {
            "schema_version": 4,
            "workspace_key": "workspace",
            "session_id": "session",
            "task_id": "task",
            "type": "context.compacted",
            "payload": {"before_tokens": 900, "after_tokens": 100},
            "occurred_at": "2026-01-01T00:00:01Z",
        }
    )
    assert cancelled.payload["status"] == "cancelled"
    assert compact.type == "context.compacted"


def test_load_replay错误包含行号(tmp_path):
    path = tmp_path / "broken.jsonl"
    path.write_text("{}\nnot-json\n", encoding="utf-8")
    with pytest.raises(ReplayFormatError, match="Line 1"):
        load_replay(path)


def test_replay_service在submit返回前发布accepted并依次消费turn():
    async def run():
        service = ReplayRuntimeService(load_replay(FIXTURE))
        await service.start()
        subscription = service.subscribe_updates()
        submit_task = asyncio.create_task(
            service.submit("first prompt", submission_id="submission")
        )
        user_message = await asyncio.wait_for(
            subscription.next_update(), timeout=1
        )
        first = await asyncio.wait_for(subscription.next_update(), timeout=1)
        accepted = await submit_task
        remaining = []
        while True:
            update = await asyncio.wait_for(subscription.next_update(), timeout=1)
            remaining.append(update)
            if update.type == "task.finished" and update.task_id == accepted.task_id:
                break
        await service.close()
        return service, accepted, user_message, first, remaining

    service, accepted, user_message, first, remaining = asyncio.run(run())
    assert user_message.type == "user.message"
    assert user_message.payload["text"] == "first prompt"
    assert accepted.task_id == "task-1"
    assert first.type == "task.accepted"
    assert service.submissions == ["first prompt"]
    assert any(item.task_id == "unrelated-task" for item in remaining)


def test_replay_subscription关闭后唤醒等待者():
    async def run():
        service = ReplayRuntimeService(load_replay(FIXTURE))
        await service.start()
        subscription = service.subscribe_updates()
        waiter = asyncio.create_task(subscription.next_update())
        await asyncio.sleep(0)
        await service.close()
        with pytest.raises(RuntimeError, match="closed"):
            await asyncio.wait_for(waiter, timeout=1)

    asyncio.run(run())
