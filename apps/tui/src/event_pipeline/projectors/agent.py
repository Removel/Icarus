"""Projection of public Agent RuntimeUpdate values."""

import json

from packages.gateway_protocol import RuntimeUpdateModel
from apps.tui.src.event_pipeline.actions import (
    AppendAssistantDelta,
    CompleteAssistantMessage,
    AppendError,
    AppendToolStarted,
    UiAction,
    UpdateToolCompleted,
)


class AgentProjector:
    def project(
        self, update: RuntimeUpdateModel
    ) -> tuple[UiAction, ...] | None:
        task_id = update.task_id
        if task_id is None:
            return ()
        payload = update.payload
        if update.type == "assistant.text_delta":
            text = str(payload.get("text", ""))
            return (AppendAssistantDelta(task_id, text),) if text else ()
        if update.type == "assistant.message":
            text = str(payload.get("text", ""))
            return (CompleteAssistantMessage(task_id, text),) if text else ()
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
