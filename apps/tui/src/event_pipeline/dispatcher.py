"""Projection of public RuntimeUpdate values into TUI actions."""

from __future__ import annotations

import logging
from typing import Protocol

from packages.gateway_protocol import RuntimeUpdateModel
from apps.tui.src.event_pipeline.actions import UiAction


class UpdateProjector(Protocol):
    def project(
        self, update: RuntimeUpdateModel
    ) -> tuple[UiAction, ...] | None: ...


class ProjectorRegistry:
    def __init__(self, *, logger: logging.Logger | None = None) -> None:
        self._projectors: dict[str, UpdateProjector] = {}
        self._logger = logger or logging.getLogger("icarus.tui.projectors")
        self.unknown_update_count = 0
        self.unrelated_update_count = 0

    def register(self, update_type: str, projector: UpdateProjector) -> None:
        if not update_type.strip():
            raise ValueError("update_type cannot be empty")
        if update_type in self._projectors:
            raise ValueError(f"Projector is already registered: {update_type}")
        self._projectors[update_type] = projector

    @property
    def update_types(self) -> frozenset[str]:
        return frozenset(self._projectors)

    def project(
        self,
        update: RuntimeUpdateModel,
        *,
        active_task_id: str | None,
        include_unrelated: bool = False,
    ) -> tuple[UiAction, ...]:
        projector = self._projectors.get(update.type)
        if projector is None:
            self.unknown_update_count += 1
            self._logger.debug(
                "Ignoring unknown RuntimeUpdate: type=%s", update.type
            )
            return ()
        if not include_unrelated and update.task_id is not None and (
            active_task_id is None or update.task_id != active_task_id
        ):
            self.unrelated_update_count += 1
            self._logger.debug(
                "Ignoring unrelated RuntimeUpdate: type=%s task=%s active=%s",
                update.type,
                update.task_id,
                active_task_id,
            )
            return ()
        actions = projector.project(update)
        if actions is None:
            self.unknown_update_count += 1
            return ()
        return actions


def create_default_projector_registry(
    *, logger: logging.Logger | None = None
) -> ProjectorRegistry:
    from apps.tui.src.event_pipeline.projectors.agent import AgentProjector
    from apps.tui.src.event_pipeline.projectors.blackboard import (
        BlackboardProjector,
    )
    from apps.tui.src.event_pipeline.projectors.user_input import (
        UserInputProjector,
    )

    registry = ProjectorRegistry(logger=logger)
    agent = AgentProjector()
    for update_type in (
        "assistant.text_delta",
        "assistant.message",
        "tool.started",
        "tool.completed",
        "task.error",
        "task.usage",
    ):
        registry.register(update_type, agent)
    input_projector = UserInputProjector()
    for update_type in (
        "user.message",
        "task.accepted",
        "task.started",
        "task.finished",
    ):
        registry.register(update_type, input_projector)
    blackboard = BlackboardProjector()
    registry.register("context.compacted", blackboard)
    registry.register("session.lifecycle", blackboard)
    return registry
