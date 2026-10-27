"""Projection of non-visible Session and context RuntimeUpdate values."""

from apps.tui.src.event_pipeline.actions import UiAction
from packages.gateway_protocol import RuntimeUpdateModel


class BlackboardProjector:
    def project(
        self, update: RuntimeUpdateModel, *, historical: bool = False
    ) -> tuple[UiAction, ...] | None:
        del historical
        if update.type in {"context.compacted", "session.lifecycle"}:
            return ()
        return None
