"""Projection from public RuntimeUpdate values to TUI-owned actions."""

from apps.tui.src.event_pipeline.actions import (
    AppendAssistantDelta,
    AppendError,
    AppendToolStarted,
    AppendUserMessage,
    FinishTurn,
    SetRuntimeStatus,
    ShowNotification,
    UiAction,
    UpdateToolCompleted,
)
from apps.tui.src.event_pipeline.dispatcher import (
    ProjectorRegistry,
    UpdateProjector,
    create_default_projector_registry,
)

__all__ = [
    "AppendAssistantDelta",
    "AppendError",
    "AppendToolStarted",
    "AppendUserMessage",
    "FinishTurn",
    "ProjectorRegistry",
    "SetRuntimeStatus",
    "ShowNotification",
    "UiAction",
    "UpdateToolCompleted",
    "UpdateProjector",
    "create_default_projector_registry",
]
