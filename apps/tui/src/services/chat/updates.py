"""Synchronous stream ordering and public-update projection."""

from apps.tui.src.event_pipeline import SetRuntimeStatus


class UpdateProcessor:
    def __init__(self, projectors):
        self.projectors = projectors
        self.reset()

    def reset(self):
        self.sequence = 0
        self.completed = set()
        self.early = []

    def project(
        self, update, *, session_id, workspace_key, active_task_id, historical=False
    ):
        if session_id and (
            update.session_id != session_id or update.workspace_key != workspace_key
        ):
            return None
        sequence = update.sequence
        if sequence is not None and sequence <= self.sequence:
            return None
        if sequence is not None and not historical and sequence != self.sequence + 1:
            raise RuntimeError(
                f"Session history has a sequence gap: expected={self.sequence + 1} actual={sequence}"
            )
        step = update.payload.get("step", 1)
        key = update.task_id, step
        if update.type == "assistant.text_delta" and key in self.completed:
            # Persisted late deltas still consume their sequence, but not their content.
            if sequence is not None:
                self.sequence = sequence
            return None
        actions = self.projectors.project(
            update,
            active_task_id=active_task_id,
            include_unrelated=historical or sequence is not None,
            historical=historical,
        )
        return tuple(
            a
            for a in actions
            if not isinstance(a, SetRuntimeStatus)
            or (not historical and a.task_id == active_task_id)
        )

    def commit(self, update):
        if update.type == "assistant.message":
            self.completed.add((update.task_id, update.payload.get("step", 1)))
        if update.sequence is not None:
            self.sequence = update.sequence
