import hashlib
import json

import pytest

from apps.agent.src.agent_orchestration.plugins.skill.generation_prompt import (
    SensitiveSkillDataError,
    SkillGenerationPromptBuilder,
)
from apps.agent.src.agent_orchestration.plugins.skill.repository import SkillSnapshot
from apps.agent.src.model_provider.types import ImagePart, Message, TextPart, ToolCall


def snapshot(tmp_path):
    content = "---\nname: existing\ndescription: existing\n---\nbody"
    return SkillSnapshot(
        name="existing",
        description="existing",
        scope="global",
        path=tmp_path / "existing" / "SKILL.md",
        content=content,
        content_hash=hashlib.sha256(content.encode()).hexdigest(),
    )


def payload(prompt):
    raw = prompt.split("<skill_generation_data>\n", 1)[1].split(
        "\n</skill_generation_data>", 1
    )[0]
    return json.loads(raw)


def test_prompt_preserves_full_conversation_including_unpaired_tool_call(tmp_path):
    call = ToolCall(
        id="current-call",
        name="skill_produce",
        arguments={"name": "new-skill", "scope": "workspace"},
    )
    messages = (
        Message("user", [TextPart("earlier context")]),
        Message("assistant", [TextPart("working")]),
        Message("assistant", [], tool_calls=[call]),
    )

    result = payload(
        SkillGenerationPromptBuilder().build(
            operation="produce",
            name="new-skill",
            scope="workspace",
            instructions="capture the workflow",
            conversation=messages,
        )
    )

    assert [item["role"] for item in result["conversation"]] == [
        "user",
        "assistant",
        "assistant",
    ]
    assert result["conversation"][-1]["tool_calls"][0]["id"] == "current-call"
    assert result["name"] == "new-skill"
    assert result["scope"] == "workspace"
    assert result["instructions"] == "capture the workflow"


def test_evolve_prompt_includes_source_snapshot_and_image_metadata(tmp_path):
    result = payload(
        SkillGenerationPromptBuilder().build(
            operation="evolve",
            name="existing",
            instructions="make it safer",
            conversation=[
                Message(
                    "user",
                    [
                        ImagePart(
                            "https://example.com/a.png?token=secret#view",
                            "image/png",
                        )
                    ],
                )
            ],
            snapshot=snapshot(tmp_path),
        )
    )

    assert result["source_skill"]["content"].endswith("body")
    image = result["conversation"][0]["content"][0]
    assert image == {
        "media_type": "image/png",
        "type": "image",
        "url": "https://example.com/a.png",
    }


def test_prompt_redacts_nested_and_string_secrets():
    call = ToolCall(
        id="secret",
        name="api",
        arguments={
            "token": "argument-secret",
            "nested": {"api_key": "nested-secret"},
        },
    )
    prompt = SkillGenerationPromptBuilder(lambda value: value).build(
        operation="produce",
        name="safe",
        scope="global",
        instructions="Authorization: Bearer header-secret",
        conversation=[Message("assistant", [], tool_calls=[call])],
    )

    for secret in ("argument-secret", "nested-secret", "header-secret"):
        assert secret not in prompt
    assert prompt.count("[REDACTED]") >= 3


@pytest.mark.parametrize(
    "secret",
    [
        "-----BEGIN PRIVATE KEY-----\nabc",
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.signatureABC",
        "sk-proj-abcdefghijklmnopqrstuvwxyz123456",
        "AKIAABCDEFGHIJKLMNOP",
    ],
)
def test_prompt_fails_closed_on_strong_credentials(secret):
    with pytest.raises(SensitiveSkillDataError, match="strong credential"):
        SkillGenerationPromptBuilder().build(
            operation="produce",
            name="safe",
            scope="workspace",
            instructions="generate",
            conversation=[Message("tool", [TextPart(secret)])],
        )


def test_prompt_requires_explicit_operation_fields(tmp_path):
    builder = SkillGenerationPromptBuilder()
    with pytest.raises(ValueError, match="produce requires scope"):
        builder.build(
            operation="produce",
            name="x",
            instructions="x",
            conversation=[],
        )
    with pytest.raises(ValueError, match="evolve requires"):
        builder.build(
            operation="evolve",
            name="x",
            scope="workspace",
            instructions="x",
            conversation=[],
            snapshot=snapshot(tmp_path),
        )
