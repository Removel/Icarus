"""Projection of public Agent RuntimeUpdate values."""

import json

from packages.gateway_protocol import RuntimeUpdateModel
from apps.tui.src.event_pipeline.actions import (
    AppendAssistantDelta,
    AppendThinkingDelta,
    CompleteThinking,
    CompleteAssistantMessage,
    AppendError,
    AppendToolStarted,
    UiAction,
    UpdateToolCompleted,
)


class AgentProjector:
    def project(
        self, update: RuntimeUpdateModel, *, historical: bool = False
    ) -> tuple[UiAction, ...] | None:
        task_id = update.task_id
        if task_id is None:
            return ()
        payload = update.payload
        if update.type == "assistant.text_delta":
            step = _positive_step(payload)
            text = str(payload.get("text", ""))
            return (AppendAssistantDelta(task_id, text, step),) if text else ()
        if update.type == "assistant.message":
            step = _positive_step(payload)
            text = str(payload.get("text", ""))
            return (CompleteAssistantMessage(task_id, text, step),) if text else ()
        if update.type == "assistant.thinking_delta":
            step = _positive_step(payload)
            text = str(payload.get("text", ""))
            return (
                AppendThinkingDelta(task_id, step, text, historical),
            ) if text else ()
        if update.type == "assistant.thinking":
            step = _positive_step(payload)
            text = str(payload.get("text", ""))
            partial = payload.get("partial")
            if not isinstance(partial, bool):
                raise ValueError("assistant.thinking partial must be boolean")
            return (
                CompleteThinking(task_id, step, text, partial, historical),
            ) if text else ()
        if update.type == "tool.started":
            return (
                AppendToolStarted(
                    task_id=task_id,
                    call_id=str(payload["call_id"]),
                    tool_name=str(payload["tool_name"]),
                    arguments_json=json.dumps(
                        payload["arguments"],
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    step=_positive_step(payload),
                ),
            )
        if update.type == "tool.completed":
            success = bool(payload["success"])
            return (
                UpdateToolCompleted(
                    task_id=task_id,
                    call_id=str(payload["call_id"]),
                    tool_name=str(payload["tool_name"]),
                    success=success,
                    error=(
                        str(payload["error"])
                        if not success and payload.get("error") is not None
                        else None
                    ),
                    step=_positive_step(payload),
                    output_preview=payload.get("output_preview"),
                    preview_truncated=bool(
                        payload.get("preview_truncated", False)
                    ),
                    full_result_available=bool(
                        payload.get("full_result_available", False)
                    ),
                    preview_error=(
                        str(payload["preview_error"])
                        if payload.get("preview_error") is not None
                        else None
                    ),
                ),
            )
        if update.type == "task.error":
            return (
                AppendError(
                    task_id=task_id,
                    error_type=str(payload["error_type"]),
                    message=str(payload["message"]),
                ),
            )
        if update.type == "task.usage":
            return ()
        return None


def _positive_step(payload) -> int:
    step = payload.get("step")
    if isinstance(step, bool) or not isinstance(step, int) or step < 1:
        raise ValueError("RuntimeUpdate step must be a positive integer")
    return step
