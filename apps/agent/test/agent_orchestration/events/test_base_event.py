from dataclasses import asdict
from datetime import UTC

from apps.agent.src.agent_orchestration.events import Event


def test_event_自动生成唯一标识和utc时间():
    first = Event(correlation_id="run-1")
    second = Event(correlation_id="run-1")

    assert first.event_id != second.event_id
    assert first.occurred_at.tzinfo == UTC
    assert first.correlation_id == "run-1"
    assert asdict(first)["event_id"] == first.event_id
