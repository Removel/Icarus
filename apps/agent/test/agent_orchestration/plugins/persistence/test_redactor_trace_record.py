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
        "apiToken": "camel-secret",
        "X-Api-Key": "header-secret",
        "API_KEY": "upper-secret",
        "APIKey": "acronym-secret",
        "binary": b"abc",
    }

    result = Redactor().redact(source)

    assert result["Authorization"] == "[REDACTED]"
    assert result["nested"]["api_key"] == "[REDACTED]"
    assert result["nested"]["items"][0]["access_token"] == "[REDACTED]"
    assert result["nested"]["items"][1]["value"] == "safe"
    assert result["apiToken"] == "[REDACTED]"
    assert result["X-Api-Key"] == "[REDACTED]"
    assert result["API_KEY"] == "[REDACTED]"
    assert result["APIKey"] == "[REDACTED]"
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
            "task_id": "task-1",
            "model_role": "thinking",
        },
        data={"token": "secret", "result": {"success": True}},
    )

    record = TraceRecord.from_hook_event(event, Redactor())
    payload = json.loads(record.to_json_line())

    assert payload["task_id"] == "task-1"
    assert payload["run_id"] == "run-1"
    assert payload["context"] == {"model_role": "thinking"}
    assert payload["data"]["token"] == "[REDACTED]"
    assert "workspace_key" not in payload
    assert "session_id" not in payload
    assert record.estimated_bytes > 0


def test_redactor_清理异常文本中的常见凭据():
    redactor = Redactor()

    assert redactor.redact_text("Authorization: Bearer abc123 failed") == (
        "Authorization: [REDACTED]"
    )
    assert redactor.redact_text("url?token=abc123&mode=1") == (
        "url?token=[REDACTED]&mode=1"
    )
    assert redactor.redact_text('{"access_token":"top-secret"}') == (
        '{"access_token":"[REDACTED]"}'
    )
    assert redactor.redact_text(
        '{"authorization":"Bearer secret","result":"created","count":2}'
    ) == (
        '{"authorization":"[REDACTED]","result":"created","count":2}'
    )
    assert redactor.redact_text("Authorization: Basic dXNlcjpwYXNz") == (
        "Authorization: [REDACTED]"
    )
    assert redactor.redact_text(
        'Authorization: Digest username="u", response="secret"'
    ) == "Authorization: [REDACTED]"
    assert redactor.redact_text("client_secret=abc&refresh_token=def") == (
        "client_secret=[REDACTED]&refresh_token=[REDACTED]"
    )
    assert redactor.redact_text("Cookie: session=abc") == (
        "Cookie: [REDACTED]"
    )


def test_redactor_自定义camel_case字段使用相同归一化():
    redactor = Redactor({"signingKey"})

    assert redactor.redact({"signingKey": "secret"}) == {
        "signingKey": "[REDACTED]"
    }
