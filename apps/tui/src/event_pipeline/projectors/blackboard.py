"""Projection of non-visible Session and context RuntimeUpdate values."""

from packages.gateway_protocol import RuntimeUpdateModel
from apps.tui.src.event_pipeline.actions import UiAction


class BlackboardProjector:
    def project(
        self, update: RuntimeUpdateModel
    ) -> tuple[UiAction, ...] | None:
        if update.type in {"context.compacted", "session.lifecycle"}:
            return ()
        return None
