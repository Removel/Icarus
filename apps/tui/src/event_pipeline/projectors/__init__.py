"""Concrete source projectors shipped by the TUI."""

from apps.tui.src.event_pipeline.projectors.agent import AgentProjector
from apps.tui.src.event_pipeline.projectors.user_input import UserInputProjector

__all__ = ["AgentProjector", "UserInputProjector"]
