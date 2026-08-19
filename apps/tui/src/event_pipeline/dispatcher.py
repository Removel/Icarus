"""Explicit source registry for public runtime event projectors."""

from __future__ import annotations

import logging
from typing import Protocol

from apps.agent.src.agent_orchestration.events import Event
from apps.tui.src.event_pipeline.actions import UiAction


class EventProjector(Protocol):
    """Project one known source Event.

    ``None`` means the Event type is unknown. An empty tuple means the Event is
    recognized but intentionally has no visible projection.
    """

    def project(self, event: Event) -> tuple[UiAction, ...] | None:
        ...


class ProjectorRegistry:
    """Route Events by source identity and reject unrelated task output."""

    def __init__(self, *, logger: logging.Logger | None = None) -> None:
        self._projectors: dict[str, EventProjector] = {}
        self._logger = logger or logging.getLogger("icarus.tui.projectors")
        self.unknown_source_count = 0
        self.unknown_event_count = 0
        self.unrelated_event_count = 0

    def register(self, source_plugin_id: str, projector: EventProjector) -> None:
        if not source_plugin_id.strip():
            raise ValueError("source_plugin_id cannot be empty")
        if source_plugin_id in self._projectors:
            raise ValueError(
                f"Projector is already registered: {source_plugin_id}"
            )
        self._projectors[source_plugin_id] = projector

    @property
    def source_plugin_ids(self) -> frozenset[str]:
        return frozenset(self._projectors)

    def project(
        self,
        source_plugin_id: str,
        event: Event,
        *,
        active_task_id: str | None,
    ) -> tuple[UiAction, ...]:
        projector = self._projectors.get(source_plugin_id)
        if projector is None:
            self.unknown_source_count += 1
            self._logger.debug(
                "Ignoring event from unregistered source: source=%s type=%s",
                source_plugin_id,
                type(event).__name__,
            )
            return ()

        if active_task_id is None or event.correlation_id != active_task_id:
            self.unrelated_event_count += 1
            self._logger.debug(
                "Ignoring unrelated runtime event: source=%s type=%s correlation=%s active=%s",
                source_plugin_id,
                type(event).__name__,
                event.correlation_id,
                active_task_id,
            )
            return ()

        actions = projector.project(event)
        if actions is None:
            self.unknown_event_count += 1
            self._logger.debug(
                "Ignoring unknown event for registered source: source=%s type=%s",
                source_plugin_id,
                type(event).__name__,
            )
            return ()
        return actions


def create_default_projector_registry(
    *,
    logger: logging.Logger | None = None,
) -> ProjectorRegistry:
    """Create the explicit projector set for currently exported sources."""

    from apps.tui.src.event_pipeline.projectors.agent import AgentProjector
    from apps.tui.src.event_pipeline.projectors.user_input import (
        UserInputProjector,
    )

    registry = ProjectorRegistry(logger=logger)
    registry.register("agent", AgentProjector())
    registry.register("user-input", UserInputProjector())
    return registry
