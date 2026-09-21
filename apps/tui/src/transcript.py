"""Stable semantic transcript generated from TUI-owned actions."""

from __future__ import annotations

from apps.tui.src.event_pipeline import (
    AppendAssistantDelta,
    AppendError,
    AppendThinkingDelta,
    AppendToolStarted,
    AppendUserCorrection,
    AppendUserMessage,
    CompleteThinking,
    CompleteAssistantMessage,
    FinishTurn,
    SetRuntimeStatus,
    ShowNotification,
    UiAction,
    UpdateToolCompleted,
)
from apps.tui.src.event_pipeline.dispatcher import (
    ProjectorRegistry,
    create_default_projector_registry,
)
from apps.tui.src.replay import ReplayScenario


class TranscriptRecorder:
    """Record semantic order without terminal layout or ANSI styling."""

    def __init__(self) -> None:
        self._lines: list[str] = []
        self._assistant_parts: list[str] = []
        self._thinking_parts: dict[tuple[str, int], list[str]] = {}
        self._completed_thinking: set[tuple[str, int]] = set()

    def record(self, action: UiAction) -> None:
        if isinstance(action, AppendUserMessage):
            self._flush_assistant()
            self._lines.append("[user]")
            self._lines.extend(action.text.splitlines())
            return
        if isinstance(action, AppendAssistantDelta):
            self._assistant_parts.append(action.text)
            return
        if isinstance(action, CompleteAssistantMessage):
            self._assistant_parts = [action.text]
            return
        if isinstance(action, AppendThinkingDelta):
            key = (action.task_id, action.step)
            if key not in self._completed_thinking:
                self._flush_assistant()
                self._thinking_parts.setdefault(key, []).append(action.text)
            return
        if isinstance(action, CompleteThinking):
            key = (action.task_id, action.step)
            if key not in self._completed_thinking:
                self._flush_assistant()
                self._thinking_parts[key] = [action.text]
                self._completed_thinking.add(key)
                status = " partial" if action.partial else ""
                self._lines.append(f"[thinking step={action.step}{status}]")
                self._lines.extend(action.text.splitlines())
            return

        self._flush_assistant()
        if isinstance(action, AppendUserCorrection):
            self._lines.append(
                f"[user-correction before-step={action.applied_before_step}]"
            )
            self._lines.extend(action.text.splitlines())
        elif isinstance(action, AppendToolStarted):
            self._lines.append(
                f"[tool] {action.tool_name} {action.arguments_json}"
            )
        elif isinstance(action, UpdateToolCompleted):
            status = "success" if action.success else "failed"
            self._lines.append(
                f"[tool] {action.tool_name} completed: {status}"
            )
            if not action.success and action.error:
                self._lines.append(f"[tool-error] {action.error}")
            if action.output_preview is not None:
                self._lines.append("[tool-output]")
                self._lines.extend(
                    _format_transcript_value(action.output_preview).splitlines()
                )
            if action.preview_error:
                self._lines.append("[tool-preview] unavailable")
            if action.preview_truncated and action.full_result_available:
                self._lines.append(
                    "[tool-preview] truncated; full result remains agent-side"
                )
            elif action.preview_truncated:
                self._lines.append(
                    "[tool-preview] truncated; full result unavailable"
                )
            elif action.full_result_available:
                self._lines.append("[tool-preview] full result remains agent-side")
        elif isinstance(action, AppendError):
            self._lines.append(
                f"[error] {action.error_type}: {action.message}"
            )
        elif isinstance(action, SetRuntimeStatus):
            self._lines.append(f"[task] {action.status}: {action.text}")
        elif isinstance(action, ShowNotification):
            self._lines.append(
                f"[notification:{action.level}] {action.text}"
            )
        elif isinstance(action, FinishTurn):
            self._lines.append(f"[task] {action.status}")
        else:  # pragma: no cover - a new action must choose a transcript policy.
            raise TypeError(f"Unsupported UiAction: {type(action).__name__}")

    def render(self) -> str:
        self._flush_assistant()
        if not self._lines:
            return ""
        return "\n".join(self._lines) + "\n"

    def _flush_assistant(self) -> None:
        if not self._assistant_parts:
            return
        self._lines.append("[assistant]")
        self._lines.extend("".join(self._assistant_parts).splitlines())
        self._assistant_parts.clear()


def transcript_from_scenario(
    scenario: ReplayScenario,
    *,
    registry: ProjectorRegistry | None = None,
) -> str:
    registry = registry or create_default_projector_registry()
    recorder = TranscriptRecorder()
    for turn in scenario.turns:
        for update in turn.updates:
            for action in registry.project(
                update, active_task_id=turn.task_id
            ):
                recorder.record(action)
    return recorder.render()


def _format_transcript_value(value: object) -> str:
    if isinstance(value, str):
        return value
    import json

    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        return "[Preview unavailable]"
