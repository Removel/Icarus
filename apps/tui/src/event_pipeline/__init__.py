"""Source-aware projection from runtime events to TUI-owned actions."""

from apps.tui.src.event_pipeline.actions import (
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
    EventProjector,
    ProjectorRegistry,
    create_default_projector_registry,
)

__all__ = [
    "AppendAssistantDelta",
    "AppendError",
    "AppendToolStarted",
    "EventProjector",
    "FinishTurn",
    "ProjectorRegistry",
    "SetRuntimeStatus",
    "ShowNotification",
    "UiAction",
    "UpdateToolCompleted",
    "create_default_projector_registry",
]
