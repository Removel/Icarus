import json

from apps.agent.src.agent_orchestration.hooks.hook_event import HookEvent
from apps.agent.src.agent_orchestration.plugins.persistence import (
    Redactor,
    TraceRecord,
)


def test_redactor_递归脱敏且不修改原对象():
    source = {
        "Authorization": "Bearer secret",
        "nested": {
            "api_key": "key",
            "items": [{"access_token": "token"}, {"value": "safe"}],
        },
        "binary": b"abc",
    }

    result = Redactor().redact(source)

    assert result["Authorization"] == "[REDACTED]"
    assert result["nested"]["api_key"] == "[REDACTED]"
    assert result["nested"]["items"][0]["access_token"] == "[REDACTED]"
    assert result["nested"]["items"][1]["value"] == "safe"
    assert result["binary"] == {"type": "bytes", "size": 3}
    assert source["Authorization"] == "Bearer secret"


def test_trace_record_保留关联id但不重复workspace_session():
    event = HookEvent.create(
        name="tool.execute",
        phase="after",
        run_id="run-1",
        context={
            "workspace_key": "workspace",
            "workspace_path": "/work",
            "session_id": "session-1",
            "correlation_id": "task-1",
            "model_role": "thinking",
        },
        data={"token": "secret", "result": {"success": True}},
    )

    record = TraceRecord.from_hook_event(event, Redactor())
    payload = json.loads(record.to_json_line())

    assert payload["correlation_id"] == "task-1"
    assert payload["run_id"] == "run-1"
    assert payload["context"] == {"model_role": "thinking"}
    assert payload["data"]["token"] == "[REDACTED]"
    assert "workspace_key" not in payload
    assert "session_id" not in payload
    assert record.estimated_bytes > 0
