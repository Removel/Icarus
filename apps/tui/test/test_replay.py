import asyncio
import json
from pathlib import Path

import pytest

from apps.agent.src.agent_orchestration.plugins import InputQueuedEvent
from apps.tui.src.replay import (
    ReplayFormatError,
    ReplayRuntimeService,
    decode_replay_record,
    load_replay,
)


FIXTURE = Path(__file__).parent / "fixtures" / "synthetic_tui_events.jsonl"


def test_load_replay按终态切分三轮并保留unrelated事件():
    scenario = load_replay(FIXTURE)

    assert scenario.task_ids == ("task-1", "task-2", "task-3")
    first_task_ids = [
        event.task_id for _, event in scenario.turns[0].events
    ]
    assert "unrelated-task" in first_task_ids
    assert first_task_ids[0] == "task-1"
    assert first_task_ids[-1] == "task-1"


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"schema_version": 99}, "unsupported schema_version"),
        ({"event_type": "unknown"}, "unsupported event_type"),
        ({"payload": None}, "payload must be an object"),
    ],
)
def test_decode_replay_record严格拒绝未知或缺失字段(change, message):
    record = {
        "schema_version": 2,
        "source_plugin_id": "agent",
        "event_type": "agent_text_delta",
        "task_id": "task-1",
        "payload": {"step": 1, "text": "hello"},
    }
    record.update(change)

    with pytest.raises(ReplayFormatError, match=message):
        decode_replay_record(record)


def test_load_replay错误包含行号(tmp_path):
    path = tmp_path / "broken.jsonl"
    path.write_text("{}\nnot-json\n", encoding="utf-8")

    with pytest.raises(ReplayFormatError, match="Line 1"):
        load_replay(path)


def test_replay_service在submit返回前发布queued并依次消费turn():
    async def run():
        service = ReplayRuntimeService(load_replay(FIXTURE))
        await service.start()
        subscription = service.subscribe_events()

        submit_task = asyncio.create_task(service.submit("first prompt"))
        source, first_event = await asyncio.wait_for(
            subscription.next_event(), timeout=1
        )
        accepted = await submit_task

        remaining = []
        while True:
            item = await asyncio.wait_for(subscription.next_event(), timeout=1)
            remaining.append(item)
            if (
                item[1].task_id == accepted.task_id
                and type(item[1]).__name__ == "InputFinishedEvent"
            ):
                break
        subscription.close()
        await service.stop()
        return service, accepted, source, first_event, remaining

    service, accepted, source, first_event, remaining = asyncio.run(run())

    assert accepted.task_id == "task-1"
    assert source == "user-input"
    assert isinstance(first_event, InputQueuedEvent)
    assert first_event.task_id == accepted.task_id
    assert service.submissions == ["first prompt"]
    assert any(event.task_id == "unrelated-task" for _, event in remaining)


def test_replay_subscription关闭后唤醒等待者():
    async def run():
        service = ReplayRuntimeService(load_replay(FIXTURE))
        await service.start()
        subscription = service.subscribe_events()
        waiter = asyncio.create_task(subscription.next_event())
        await asyncio.sleep(0)
        subscription.close()
        with pytest.raises(RuntimeError, match="closed"):
            await asyncio.wait_for(waiter, timeout=1)
        await service.stop()

    asyncio.run(run())
