from datetime import UTC, datetime
import json
import pytest

from apps.agent.src.agent_orchestration.plugins.persistence import Redactor
from apps.agent.src.agent_orchestration.plugins.skill.maintenance_prompt import (
    SKILL_MAINTENANCE_SYSTEM_PROMPT,
    SensitiveMaintenanceDataError,
    SkillMaintenancePromptBuilder,
)
from apps.agent.src.agent_orchestration.plugins.skill.models import (
    SkillDefinition,
)
from apps.agent.src.agent_orchestration.plugins.skill.repository import (
    SkillSnapshot,
)
from apps.agent.src.agent_orchestration.plugins.skill.turn_state import (
    ToolCallTrace,
)
from apps.agent.src.agent_orchestration.tools import ToolExecutionResult
from apps.agent.src.model_provider.types import (
    ImagePart,
    Message,
    TextPart,
    ToolCall,
)


def skill(tmp_path, name, *, scope="workspace"):
    return SkillDefinition(
        name=name,
        description=f"description for {name}",
        path=tmp_path / name / "SKILL.md",
        scope=scope,
        metadata={"category": "test"},
    )


def snapshot(tmp_path, name):
    return SkillSnapshot(
        name=name,
        description=f"snapshot for {name}",
        scope="workspace",
        path=tmp_path / name / "SKILL.md",
        content=f"---\nname: {name}\ndescription: snapshot\n---\nbody",
        content_hash=f"hash-{name}",
        lifecycle_status="archived",
        last_used_at=datetime(2026, 8, 1, 12, tzinfo=UTC),
        use_count=7,
    )


def extract_payload(prompt):
    document = prompt.split("<skill_maintenance_data>\n", 1)[1].split(
        "\n</skill_maintenance_data>", 1
    )[0]
    return json.loads(document)


def test_maintenance_prompt稳定保留完整消息轨迹skill和快照(tmp_path):
    call = ToolCall(
        id="call-1",
        name="write",
        arguments={"path": "skills/new/SKILL.md", "value": 1},
    )
    messages = [
        Message("system", [TextPart("stable system")]),
        Message(
            "user",
            [
                TextPart("build it"),
                ImagePart(
                    url="https://example.com/reference.png",
                    media_type="image/png",
                ),
            ],
        ),
        Message("assistant", [], tool_calls=[call]),
        Message(
            "tool",
            [TextPart("written")],
            tool_call_id="call-1",
        ),
        Message("assistant", [TextPart("done")]),
    ]
    trace = ToolCallTrace(
        step=2,
        tool_call=call,
        result=ToolExecutionResult(
            success=True,
            output={"path": tmp_path / "new", "bytes": b"abc"},
        ),
    )
    matched = skill(tmp_path, "matched")
    accumulated = skill(tmp_path, "accumulated", scope="global")
    current = snapshot(tmp_path, "current")
    builder = SkillMaintenancePromptBuilder(Redactor())

    first = builder.build(
        messages=messages,
        tool_trace=[trace],
        matched_skills=[matched],
        session_skills=[matched, accumulated],
        skill_snapshots=[current],
    )
    second = builder.build(
        messages=messages,
        tool_trace=[trace],
        matched_skills=[matched],
        session_skills=[matched, accumulated],
        skill_snapshots=[current],
    )
    payload = extract_payload(first)

    assert first == second
    assert [message["role"] for message in payload["conversation_messages"]] == [
        "system",
        "user",
        "assistant",
        "tool",
        "assistant",
    ]
    assert payload["conversation_messages"][1]["content"][1] == {
        "media_type": "image/png",
        "type": "image",
        "url": "https://example.com/reference.png",
    }
    assert payload["conversation_messages"][2]["tool_calls"][0] == {
        "arguments": {"path": "skills/new/SKILL.md", "value": 1},
        "id": "call-1",
        "name": "write",
    }
    assert payload["tool_trajectory"][0]["step"] == 2
    result = payload["tool_trajectory"][0]["result"]
    assert result["error"] is None
    assert result["success"] is True
    assert result["output"]["bytes"] == {"size": 3, "type": "bytes"}
    assert result["output"]["path"].endswith("/new")
    assert [item["name"] for item in payload["matched_skills"]] == [
        "matched"
    ]
    assert [item["name"] for item in payload["session_skills"]] == [
        "matched",
        "accumulated",
    ]
    snapshot_payload = payload["skill_snapshots"][0]
    assert snapshot_payload["path"].endswith("/current/SKILL.md")
    assert {
        key: value
        for key, value in snapshot_payload.items()
        if key != "path"
    } == {
        "content": current.content,
        "content_hash": "hash-current",
        "description": "snapshot for current",
        "last_used_at": "2026-08-01T12:00:00+00:00",
        "lifecycle_status": "archived",
        "name": "current",
        "scope": "workspace",
        "use_count": 7,
    }
    assert payload["output_schema"]["title"] == "SkillMaintenancePlan"
    rules = " ".join(payload["maintenance_rules"])
    assert "never duplicate completed work" in rules
    assert "Global Skills are read-only" in rules
    assert "no_op" in rules
    assert SkillMaintenancePromptBuilder.system_prompt == (
        SKILL_MAINTENANCE_SYSTEM_PROMPT
    )


def test_maintenance_prompt在json序列化前调用注入的callable(tmp_path):
    observed = []

    def redact(value):
        assert isinstance(value, dict)
        assert value["conversation_messages"][0]["content"][0]["text"] == (
            "original"
        )
        observed.append(value)
        copy = dict(value)
        copy["conversation_messages"] = [
            {"role": "user", "content": [{"text": "replaced"}]}
        ]
        return copy

    prompt = SkillMaintenancePromptBuilder(redact).build(
        messages=[Message("user", [TextPart("original")])],
        tool_trace=[],
        matched_skills=[],
        session_skills=[],
        skill_snapshots=[snapshot(tmp_path, "one")],
    )

    assert len(observed) == 1
    assert "original" not in prompt
    assert "replaced" in prompt


def test_maintenance_prompt注入identity_callable仍执行字段脱敏保底(tmp_path):
    call = ToolCall(
        id="call-fields",
        name="api",
        arguments={
            "accessToken": "camel-token",
            "privateKey": "camel-key",
            "clientSecret": "camel-secret",
            "dbPassword": "camel-password",
            "sessionCookie": "camel-cookie",
        },
    )

    prompt = SkillMaintenancePromptBuilder(lambda value: value).build(
        messages=[Message("assistant", [], tool_calls=[call])],
        tool_trace=[],
        matched_skills=[],
        session_skills=[],
        skill_snapshots=[snapshot(tmp_path, "safe")],
    )

    for secret in [
        "camel-token",
        "camel-key",
        "camel-secret",
        "camel-password",
        "camel-cookie",
    ]:
        assert secret not in prompt
    assert prompt.count("[REDACTED]") >= 5


def test_maintenance_prompt脱敏嵌套字段和stdout字符串秘密(tmp_path):
    call = ToolCall(
        id="call-secret",
        name="bash",
        arguments={
            "token": "argument-token",
            "nested": {
                "api_key": "argument-api-key",
                "authorization": "Bearer argument-auth",
            },
        },
    )
    stdout = (
        "OPENAI_API_KEY=sk-output-secret\n"
        "GITHUB_TOKEN='gh-output-secret'\n"
        "DB_PASSWORD: database-secret\n"
        "SESSION_COOKIE=session-secret\n"
        "Authorization: Bearer header-secret\n"
        "AUTHORIZATION=Basic basic-secret\n"
        "Authorization=custom-secret\n"
        "curl -H x-test Bearer standalone-secret safe-output"
    )
    trace = ToolCallTrace(
        step=1,
        tool_call=call,
        result=ToolExecutionResult(
            success=False,
            output={"stdout": stdout, "api_key": "result-api-key"},
            error="Authorization: Bearer error-secret",
        ),
    )

    prompt = SkillMaintenancePromptBuilder(Redactor()).build(
        messages=[Message("assistant", [], tool_calls=[call])],
        tool_trace=[trace],
        matched_skills=[],
        session_skills=[],
        skill_snapshots=[snapshot(tmp_path, "safe")],
    )

    for secret in [
        "argument-token",
        "argument-api-key",
        "argument-auth",
        "sk-output-secret",
        "gh-output-secret",
        "database-secret",
        "session-secret",
        "header-secret",
        "basic-secret",
        "custom-secret",
        "standalone-secret",
        "result-api-key",
        "error-secret",
    ]:
        assert secret not in prompt
    assert "safe-output" in prompt
    assert prompt.count("[REDACTED]") >= 11


def test_maintenance_prompt移除图片和正文url的query与fragment(tmp_path):
    prompt = SkillMaintenancePromptBuilder().build(
        messages=[
            Message(
                "user",
                [
                    TextPart(
                        "download https://example.com/file?X-Amz-Signature=secret#part"
                    ),
                    ImagePart(
                        url="https://images.example.com/a.png?token=secret#view",
                        media_type="image/png",
                    ),
                ],
            )
        ],
        tool_trace=[],
        matched_skills=[],
        session_skills=[],
        skill_snapshots=[snapshot(tmp_path, "safe")],
    )

    assert "X-Amz-Signature" not in prompt
    assert "token=secret" not in prompt
    assert "https://example.com/file" in prompt
    assert "https://images.example.com/a.png" in prompt


@pytest.mark.parametrize(
    "secret",
    [
        "-----BEGIN PRIVATE KEY-----\nabc",
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.signatureABC",
        "sk-proj-abcdefghijklmnopqrstuvwxyz123456",
        "AKIAABCDEFGHIJKLMNOP",
    ],
)
def test_maintenance_prompt发现强凭证时拒绝整轮维护(tmp_path, secret):
    with pytest.raises(
        SensitiveMaintenanceDataError,
        match="strong credential marker",
    ):
        SkillMaintenancePromptBuilder().build(
            messages=[Message("tool", [TextPart(secret)])],
            tool_trace=[],
            matched_skills=[],
            session_skills=[],
            skill_snapshots=[snapshot(tmp_path, "safe")],
        )


def test_maintenance_prompt无标签高熵token保守脱敏(tmp_path):
    opaque = "aB3dE5fG7hJ9kL2mN4pQ6rS8tU0vW1xY2z"
    prompt = SkillMaintenancePromptBuilder().build(
        messages=[Message("tool", [TextPart(f"value {opaque}")])],
        tool_trace=[],
        matched_skills=[],
        session_skills=[],
        skill_snapshots=[snapshot(tmp_path, "safe")],
    )

    assert opaque not in prompt
    assert "[REDACTED]" in prompt


def test_maintenance_prompt脱敏url_path中的高熵capability(tmp_path):
    capability = "aB3dE5fG7hJ9kL2mN4pQ6rS8tU0vW1xY2z"
    prompt = SkillMaintenancePromptBuilder().build(
        messages=[
            Message(
                "user",
                [TextPart(f"https://example.com/download/{capability}")],
            )
        ],
        tool_trace=[],
        matched_skills=[],
        session_skills=[],
        skill_snapshots=[snapshot(tmp_path, "safe")],
    )

    assert capability not in prompt
    assert "https://example.com/download/[REDACTED]" in prompt


def test_maintenance_prompt脱敏包含斜杠的base64凭据(tmp_path):
    credential = "aB3dE5fG7hJ9kL2mN4pQ6rS8/tU0vW1xY2z=="
    prompt = SkillMaintenancePromptBuilder().build(
        messages=[Message("tool", [TextPart(f"value={credential}")])],
        tool_trace=[],
        matched_skills=[],
        session_skills=[],
        skill_snapshots=[snapshot(tmp_path, "safe")],
    )

    assert credential not in prompt
    assert "[REDACTED]" in prompt


def test_maintenance_prompt不信任名为path的字段(tmp_path):
    jwt = (
        "eyJhbGciOiJIUzI1NiJ9."
        "eyJzdWIiOiIxMjM0NTY3ODkwIn0."
        "signatureABC"
    )
    call = ToolCall(
        id="path-secret",
        name="custom",
        arguments={"path": jwt},
    )

    with pytest.raises(SensitiveMaintenanceDataError):
        SkillMaintenancePromptBuilder().build(
            messages=[Message("assistant", [], tool_calls=[call])],
            tool_trace=[],
            matched_skills=[],
            session_skills=[],
            skill_snapshots=[snapshot(tmp_path, "safe")],
        )
