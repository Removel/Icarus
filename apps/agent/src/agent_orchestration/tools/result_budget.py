"""Deterministic Tool Result serialization and bounded previews."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
import json
import math
from typing import Any

from apps.agent.src.agent_orchestration.tools.result_store import (
    StoredToolResult,
)
from apps.agent.src.agent_orchestration.tools.types import ToolExecutionResult


TokenCounter = Callable[[str], int]
DEFAULT_OMISSION_MARKER = "complete Tool Result was not saved"


def conservative_token_count(text: str) -> int:
    return max(len(text), math.ceil(len(text.encode("utf-8")) / 3))


def token_counter_with_fallback(counter: TokenCounter) -> TokenCounter:
    failed = False

    def count(text: str) -> int:
        nonlocal failed
        if failed:
            return conservative_token_count(text)
        try:
            value = counter(text)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError("Token counter must return a non-negative integer")
            return value
        except Exception:
            failed = True
            return conservative_token_count(text)

    return count


def serialize_tool_result(
    result: ToolExecutionResult, *, pretty: bool = False
) -> str:
    return json.dumps(
        result.as_dict(),
        ensure_ascii=False,
        default=str,
        indent=2 if pretty else None,
    )


def preview_json_value(
    value: Any,
    *,
    max_tokens: int,
    counter: TokenCounter = conservative_token_count,
    head_ratio: float = 0.5,
    omission_marker: str = DEFAULT_OMISSION_MARKER,
) -> tuple[Any, bool]:
    """Return a deterministic Head/Tail preview of one JSON-compatible value."""

    if max_tokens < 1:
        raise ValueError("max_tokens must be positive")
    if not 0 <= head_ratio <= 1:
        raise ValueError("head_ratio must be between 0 and 1")
    counter = token_counter_with_fallback(counter)
    if counter(_json(value)) <= max_tokens:
        return value, False
    preview = _preview_value(
        value,
        max_tokens,
        counter,
        None,
        head_ratio,
        omission_marker=omission_marker,
    )
    if counter(_json(preview)) > max_tokens:
        raise ValueError("omission marker exceeds preview budget")
    return preview, True


def render_tool_result(
    result: ToolExecutionResult,
    *,
    max_tokens: int,
    preview_tokens: int,
    counter: TokenCounter = conservative_token_count,
    stored: StoredToolResult | None = None,
    head_ratio: float = 0.5,
) -> ToolExecutionResult:
    counter = token_counter_with_fallback(counter)
    original_text = serialize_tool_result(result)
    original_tokens = counter(original_text)
    if original_tokens <= max_tokens:
        return replace(
            result,
            metadata={
                **result.metadata,
                "original_tokens": original_tokens,
                "visible_tokens": original_tokens,
                "result_truncated": False,
            },
        )
    target = max(1, min(preview_tokens, max_tokens))
    low, high = 0, target
    best: ToolExecutionResult | None = None
    while low <= high:
        content_budget = (low + high) // 2
        preview = _preview_result(
            result, content_budget, counter, stored, head_ratio
        )
        visible = counter(serialize_tool_result(preview))
        if visible <= max_tokens:
            best = replace(
                preview,
                metadata={
                    **preview.metadata,
                    "original_tokens": original_tokens,
                    "visible_tokens": visible,
                    "result_truncated": True,
                    **_stored_metadata(stored),
                },
            )
            low = content_budget + 1
        else:
            high = content_budget - 1
    if best is not None:
        return best
    minimal = ToolExecutionResult(
        success=result.success,
        output=_minimal_value(result.output, stored),
        error=_minimal_text(result.error, stored) if result.error else None,
        images=result.images,
        metadata={
            **result.metadata,
            "original_tokens": original_tokens,
            "result_truncated": True,
            "context_budget_exhausted": True,
            **_stored_metadata(stored),
        },
    )
    visible = counter(serialize_tool_result(minimal))
    return replace(
        minimal,
        metadata={
            **minimal.metadata,
            "visible_tokens": visible,
            "context_budget_exhausted": visible > max_tokens,
        },
    )


def _preview_result(result, budget, counter, stored, head_ratio):
    output_budget = max(0, budget * 4 // 5)
    error_budget = max(0, budget - output_budget)
    return ToolExecutionResult(
        success=result.success,
        output=_preview_value(
            result.output, output_budget, counter, stored, head_ratio
        ),
        error=(
            _preview_string(
                result.error, error_budget, counter, stored, head_ratio
            )
            if result.error
            else None
        ),
        images=result.images,
        metadata=result.metadata,
    )


def _preview_value(
    value,
    budget,
    counter,
    stored,
    head_ratio,
    depth=0,
    omission_marker=None,
):
    if counter(_json(value)) <= budget:
        return value
    if depth >= 8:
        return _minimal_value(value, stored, omission_marker)
    if isinstance(value, str):
        return _preview_string(
            value, budget, counter, stored, head_ratio, omission_marker
        )
    if isinstance(value, (list, tuple)):
        return _preview_sequence(
            list(value), budget, counter, stored, head_ratio, depth,
            omission_marker,
        )
    if isinstance(value, dict):
        return _preview_mapping(
            value, budget, counter, stored, head_ratio, depth, omission_marker
        )
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _preview_string(
        str(value), budget, counter, stored, head_ratio, omission_marker
    )


def _preview_string(
    value, budget, counter, stored, head_ratio, omission_marker=None
):
    marker = _marker_text(
        stored,
        omitted_tokens=counter(value),
        omission_marker=omission_marker,
    )
    if budget <= counter(marker):
        return marker
    allowance = max(0, budget - counter(marker))
    head = int(allowance * head_ratio)
    tail = allowance - head
    omitted = value[head : len(value) - tail if tail else len(value)]
    marker = _marker_text(
        stored,
        omitted_tokens=counter(omitted),
        omission_marker=omission_marker,
    )
    candidate = value[:head] + marker + (value[-tail:] if tail else "")
    while candidate and counter(_json(candidate)) > budget and (head or tail):
        if head and (not tail or head / max(1, head + tail) >= head_ratio):
            head -= 1
        elif tail:
            tail -= 1
        omitted = value[head : len(value) - tail if tail else len(value)]
        marker = _marker_text(
            stored,
            omitted_tokens=counter(omitted),
            omission_marker=omission_marker,
        )
        candidate = value[:head] + marker + (value[-tail:] if tail else "")
    return candidate


def _preview_sequence(
    values, budget, counter, stored, head_ratio, depth, omission_marker=None
):
    key = _omitted_key(values)
    original_tokens = counter(_json(values))
    kept_head, kept_tail = [], []
    left, right = 0, len(values) - 1
    head_blocked = tail_blocked = False
    while left <= right and not (head_blocked and tail_blocked):
        choose_head = (
            tail_blocked
            or not head_blocked
            and _choose_head(kept_head, kept_tail, counter, head_ratio)
        )
        target = kept_head if choose_head else kept_tail
        item = values[left] if target is kept_head else values[right]
        next_left = left + (1 if target is kept_head else 0)
        next_right = right - (1 if target is kept_tail else 0)
        marker = {
            key: _marker_payload(
                stored,
                items=max(0, next_right - next_left + 1),
                tokens=original_tokens,
                omission_marker=omission_marker,
            )
        }
        candidate = _sequence_candidate(
            kept_head, kept_tail, item, marker, target is kept_head
        )
        if counter(_json(candidate)) > budget:
            reduced = _preview_value(
                item, max(1, budget // 3), counter, stored, head_ratio,
                depth + 1, omission_marker
            )
            candidate = _sequence_candidate(
                kept_head, kept_tail, reduced, marker, target is kept_head
            )
            if counter(_json(candidate)) > budget:
                if target is kept_head:
                    head_blocked = True
                else:
                    tail_blocked = True
                continue
            item = reduced
        target.append(item)
        if target is kept_head:
            left += 1
        else:
            right -= 1
    marker = {
        key: _marker_payload(
            stored,
            items=max(0, right - left + 1),
            tokens=_slice_tokens(values, left, right, counter),
            omission_marker=omission_marker,
        )
    }
    return [*kept_head, marker, *reversed(kept_tail)]


def _preview_mapping(
    value, budget, counter, stored, head_ratio, depth, omission_marker=None
):
    items = list(value.items())
    if len(items) == 1:
        field, child = items[0]
        low, high = 0, budget
        best = {field: _minimal_value(child, stored, omission_marker)}
        while low <= high:
            child_budget = (low + high) // 2
            candidate = {
                field: _preview_value(
                    child, child_budget, counter, stored, head_ratio,
                    depth + 1, omission_marker
                )
            }
            if counter(_json(candidate)) <= budget:
                best = candidate
                low = child_budget + 1
            else:
                high = child_budget - 1
        return best
    key = _omitted_key([value])
    original_tokens = counter(_json(value))
    head, tail = [], []
    left, right = 0, len(items) - 1
    head_blocked = tail_blocked = False
    while left <= right and not (head_blocked and tail_blocked):
        choose_head = (
            tail_blocked
            or not head_blocked
            and _choose_head(dict(head), dict(tail), counter, head_ratio)
        )
        target = head if choose_head else tail
        item = items[left] if target is head else items[right]
        next_left = left + (1 if target is head else 0)
        next_right = right - (1 if target is tail else 0)
        marker = (
            key,
            _marker_payload(
                stored,
                fields=max(0, next_right - next_left + 1),
                tokens=original_tokens,
                omission_marker=omission_marker,
            ),
        )
        sequence = (
            [*head, item, marker, *reversed(tail)]
            if target is head
            else [*head, marker, item, *reversed(tail)]
        )
        candidate = dict(sequence)
        if counter(_json(candidate)) > budget:
            child_budget = max(1, budget // 3)
            reduced = (item[0], _preview_value(
                item[1], child_budget, counter, stored, head_ratio,
                depth + 1, omission_marker
            ))
            sequence = (
                [*head, reduced, marker, *reversed(tail)]
                if target is head
                else [*head, marker, reduced, *reversed(tail)]
            )
            if counter(_json(dict(sequence))) > budget:
                if target is head:
                    head_blocked = True
                else:
                    tail_blocked = True
                continue
            item = reduced
        target.append(item)
        if target is head:
            left += 1
        else:
            right -= 1
    marker = (
        key,
        _marker_payload(
            stored,
            fields=max(0, right - left + 1),
            tokens=_mapping_slice_tokens(items, left, right, counter),
            omission_marker=omission_marker,
        ),
    )
    return dict([*head, marker, *reversed(tail)])


def _omitted_key(values) -> str:
    used = {key for value in values if isinstance(value, dict) for key in value}
    index = 1
    while True:
        key = "_icarus_omitted" if index == 1 else f"_icarus_omitted_{index}"
        if key not in used:
            return key
        index += 1


def _marker_payload(stored, *, omission_marker=None, **counts):
    if omission_marker is not None:
        return {**counts, "note": omission_marker}
    if stored is None:
        reference = {"note": "complete Tool Result was not saved"}
    elif stored.complete:
        reference = {"path": str(stored.path), "complete": True}
    else:
        reference = {
            "path": str(stored.path),
            "complete": False,
            "note": "Tool Result file is incomplete",
        }
    return {**counts, **reference}


def _marker_text(stored, *, omitted_tokens=None, omission_marker=None):
    count = (
        f"; approximately {omitted_tokens} tokens omitted"
        if omitted_tokens is not None
        else ""
    )
    if omission_marker is not None:
        return f"\n... content omitted{count}; {omission_marker} ...\n"
    if stored and stored.complete:
        return f"\n... content omitted{count}; full Tool Result: {stored.path} ...\n"
    if stored is not None:
        return (
            f"\n... content omitted{count}; incomplete Tool Result file: "
            f"{stored.path} ...\n"
        )
    return (
        f"\n... content omitted{count}; "
        "complete Tool Result was not saved ...\n"
    )


def _minimal_value(value, stored, omission_marker=None):
    marker = _marker_payload(stored, omission_marker=omission_marker)
    if isinstance(value, str):
        return _marker_text(stored, omission_marker=omission_marker)
    if isinstance(value, (list, tuple)):
        return [{_omitted_key(list(value)): marker}]
    if isinstance(value, dict):
        return {_omitted_key([value]): marker}
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _marker_text(stored, omission_marker=omission_marker)


def _minimal_text(value, stored):
    del value
    return _marker_text(stored).strip()


def _stored_metadata(stored):
    if stored is None:
        return {}
    return {
        "result_file": str(stored.path),
        "result_file_complete": stored.complete,
        "original_bytes": stored.original_bytes,
        "saved_bytes": stored.saved_bytes,
    }


def _json(value):
    return json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))


def _choose_head(head, tail, counter, head_ratio):
    head_tokens = counter(_json(head)) if head else 0
    tail_tokens = counter(_json(tail)) if tail else 0
    total = head_tokens + tail_tokens
    return total == 0 or head_tokens / total < head_ratio


def _sequence_candidate(head, tail, item, marker, choose_head):
    if choose_head:
        return [*head, item, marker, *reversed(tail)]
    return [*head, marker, item, *reversed(tail)]


def _slice_tokens(values, left, right, counter):
    if left > right:
        return 0
    return counter(_json(values[left : right + 1]))


def _mapping_slice_tokens(items, left, right, counter):
    if left > right:
        return 0
    return counter(_json(dict(items[left : right + 1])))
