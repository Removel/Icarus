from dataclasses import fields
from datetime import UTC, datetime

from apps.agent.src.agent_orchestration.plugins.process import ProcessSnapshot


def test_process_snapshot只暴露稳定公共字段且可json序列化():
    now = datetime.now(UTC)
    snapshot = ProcessSnapshot(
        process_id="proc_1",
        pid=123,
        command="pnpm run dev",
        workdir="/workspace",
        status="running",
        started_at=now,
    )

    assert {item.name for item in fields(snapshot)}.isdisjoint(
        {"process", "process_group_id", "supervisor_task", "wake"}
    )
    assert snapshot.to_dict() == {
        "process_id": "proc_1",
        "pid": 123,
        "command": "pnpm run dev",
        "workdir": "/workspace",
        "status": "running",
        "started_at": now.isoformat().replace("+00:00", "Z"),
        "ended_at": None,
        "exit_code": None,
        "stop_reason": None,
        "log_truncated": False,
        "origin_task_id": None,
        "log_path": None,
    }
