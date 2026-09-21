"""Build bounded, JSON-safe Tool output projections for public history."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, fields, is_dataclass
import enum
import math
from pathlib import Path
from typing import Any

from apps.agent.src.agent_orchestration.plugins.persistence.redactor import (
    Redactor,
)
from apps.agent.src.agent_orchestration.tools.result_budget import (
    preview_json_value,
)
from apps.agent.src.agent_orchestration.tools.types import ToolExecutionResult


PUBLIC_TOOL_PREVIEW_MAX_TOKENS = 2_000
PUBLIC_OMISSION_MARKER = "additional Tool output omitted"
INTERNAL_RESULT_REFERENCE = "[Agent-side Tool Result]"
UNSUPPORTED_VALUE = "[Unsupported value]"
PREVIEW_UNAVAILABLE = "Tool output preview unavailable"


@dataclass(frozen=True)
class PublicToolProjection:
    output_preview: Any
    preview_truncated: bool
    full_result_available: bool
    preview_error: str | None
    safe_error: str | None


def build_public_tool_projection(
    result: ToolExecutionResult,
    redactor: Redactor,
) -> PublicToolProjection:
    result_file = _result_file(result)
    normalized = _to_public_value(result.output)
    without_reference = _remove_result_reference(normalized, result_file)
    redacted = redactor.redact(without_reference)
    output_preview, truncated = preview_json_value(
        redacted,
        max_tokens=PUBLIC_TOOL_PREVIEW_MAX_TOKENS,
        omission_marker=PUBLIC_OMISSION_MARKER,
    )
    return PublicToolProjection(
        output_preview=output_preview,
        preview_truncated=(
            result.metadata.get("result_truncated") is True or truncated
        ),
        full_result_available=full_result_available(result),
        preview_error=None,
        safe_error=safe_tool_error(result, redactor),
    )


def full_result_available(result: ToolExecutionResult) -> bool:
    return (
        _result_file(result) is not None
        and result.metadata.get("result_file_complete") is True
    )


def safe_tool_error(
    result: ToolExecutionResult, redactor: Redactor
) -> str | None:
    if result.error is None:
        return None
    without_reference = _remove_result_reference(
        result.error, _result_file(result)
    )
    return redactor.redact_text(without_reference)


def _result_file(result: ToolExecutionResult) -> str | None:
    value = result.metadata.get("result_file")
    if not isinstance(value, (str, Path)):
        return None
    text = str(value)
    return text if text.strip() else None


def _remove_result_reference(value: Any, result_file: str | None) -> Any:
    if result_file is None:
        return value
    if isinstance(value, str):
        return value.replace(result_file, INTERNAL_RESULT_REFERENCE)
    if isinstance(value, list):
        return [
            _remove_result_reference(item, result_file) for item in value
        ]
    if isinstance(value, dict):
        return {
            key.replace(result_file, INTERNAL_RESULT_REFERENCE): (
                _remove_result_reference(item, result_file)
            )
            for key, item in value.items()
        }
    return value


def _to_public_value(value: Any, *, depth: int = 0) -> Any:
    if depth >= 32:
        return UNSUPPORTED_VALUE
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else UNSUPPORTED_VALUE
    if isinstance(value, bytes):
        return {"type": "bytes", "size": len(value)}
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, enum.Enum):
        return _to_public_value(value.value, depth=depth + 1)
    if is_dataclass(value) and not isinstance(value, type):
        return {
            item.name: _to_public_value(
                getattr(value, item.name), depth=depth + 1
            )
            for item in fields(value)
        }
    if isinstance(value, Mapping):
        return {
            _public_key(key): _to_public_value(item, depth=depth + 1)
            for key, item in value.items()
        }
    if isinstance(value, Sequence) and not isinstance(
        value, (str, bytes, bytearray)
    ):
        return [
            _to_public_value(item, depth=depth + 1) for item in value
        ]
    return UNSUPPORTED_VALUE


def _public_key(value: Any) -> str:
    if isinstance(value, enum.Enum):
        value = value.value
    if value is None or isinstance(value, (str, bool, int)):
        return str(value)
    if isinstance(value, float) and math.isfinite(value):
        return str(value)
    if isinstance(value, Path):
        return str(value)
    return "[Unsupported key]"
