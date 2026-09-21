from dataclasses import dataclass
import json
from pathlib import Path

from apps.agent.src.agent_orchestration.plugins.persistence.redactor import (
    Redactor,
)
from apps.agent.src.agent_orchestration.plugins.runtime_update.tool_preview import (
    INTERNAL_RESULT_REFERENCE,
    PUBLIC_TOOL_PREVIEW_MAX_TOKENS,
    UNSUPPORTED_VALUE,
    build_public_tool_projection,
)
from apps.agent.src.agent_orchestration.tools.result_budget import (
    conservative_token_count,
)
from apps.agent.src.agent_orchestration.tools.types import ToolExecutionResult


def test_tool_preview保持json值并递归脱敏():
    result = ToolExecutionResult(
        True,
        {
            "items": [1, True, None, "ok"],
            "api_key": "top-secret",
            "nested": {"message": "Authorization: Bearer secret"},
        },
    )

    projection = build_public_tool_projection(result, Redactor())

    assert projection.output_preview == {
        "items": [1, True, None, "ok"],
        "api_key": "[REDACTED]",
        "nested": {"message": "Authorization: [REDACTED]"},
    }
    json.dumps(projection.output_preview)
    assert projection.preview_truncated is False
    assert projection.full_result_available is False
    assert projection.preview_error is None


def test_tool_preview移除完整结果路径且只公开允许的metadata(tmp_path):
    result_file = tmp_path / "sessions/private/tool-results/result.json"
    result = ToolExecutionResult(
        False,
        {"message": f"full result: {result_file}"},
        error=f"read {result_file} with token=secret",
        metadata={
            "result_file": str(result_file),
            "result_file_complete": True,
            "private_metadata": "must-not-leak",
        },
    )

    projection = build_public_tool_projection(result, Redactor())
    encoded = json.dumps(projection.output_preview)

    assert str(result_file) not in encoded
    assert INTERNAL_RESULT_REFERENCE in encoded
    assert projection.full_result_available is True
    assert str(result_file) not in projection.safe_error
    assert "secret" not in projection.safe_error
    assert "private_metadata" not in repr(projection)


def test_tool_preview长结果再次限长并保留头尾():
    projection = build_public_tool_projection(
        ToolExecutionResult(True, "A" * 3000 + "Z" * 3000),
        Redactor(),
    )

    assert projection.preview_truncated is True
    assert projection.output_preview.startswith("A")
    assert projection.output_preview.endswith("Z")
    assert conservative_token_count(
        json.dumps(projection.output_preview, ensure_ascii=False)
    ) <= PUBLIC_TOOL_PREVIEW_MAX_TOKENS


def test_tool_preview保留guard截断事实和完整结果可用性():
    projection = build_public_tool_projection(
        ToolExecutionResult(
            True,
            "short",
            metadata={
                "result_truncated": True,
                "result_file": Path("/private/result.json"),
                "result_file_complete": True,
            },
        ),
        Redactor(),
    )

    assert projection.preview_truncated is True
    assert projection.full_result_available is True


def test_tool_preview未知对象不调用repr():
    class Dangerous:
        def __repr__(self):
            raise AssertionError("repr must not be called")

    @dataclass
    class Container:
        value: object

    projection = build_public_tool_projection(
        ToolExecutionResult(True, Container(Dangerous())),
        Redactor(),
    )

    assert projection.output_preview == {"value": UNSUPPORTED_VALUE}
