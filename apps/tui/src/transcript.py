"""Stable semantic transcript generated from TUI-owned actions."""

from __future__ import annotations

from apps.tui.src.event_pipeline import (
    AppendAssistantDelta,
    AppendError,
    AppendToolStarted,
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

    def record(self, action: UiAction) -> None:
        if isinstance(action, AppendAssistantDelta):
            self._assistant_parts.append(action.text)
            return

        self._flush_assistant()
        if isinstance(action, AppendToolStarted):
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
        for source, event in turn.events:
            for action in registry.project(
                source, event, active_task_id=turn.task_id
            ):
                recorder.record(action)
    return recorder.render()
