"""Compact runtime phase, queue count, and help projection."""

from textual.widgets import Static

from apps.tui.src.chat_state import RuntimePhase


class RuntimeStatusBar(Static):
    def __init__(self, *, id: str | None = None) -> None:
        super().__init__(markup=False, id=id)
        self.phase = RuntimePhase.STARTING
        self.pending_count = 0
        self.status_message = ""
        self.refresh_status()

    def set_status(
        self,
        phase: RuntimePhase,
        *,
        pending_count: int,
        message: str = "",
    ) -> None:
        self.phase = phase
        self.pending_count = pending_count
        self.status_message = message
        self.refresh_status()

    def refresh_status(self) -> None:
        labels = {
            RuntimePhase.STARTING: "Starting",
            RuntimePhase.READY: "Ready",
            RuntimePhase.RUNNING: "Running",
            RuntimePhase.STOPPING: "Stopping",
            RuntimePhase.FAILED: "Failed",
        }
        parts = [labels[self.phase]]
        if self.pending_count:
            parts.append(f"Queued {self.pending_count}")
        if self.status_message:
            parts.append(self.status_message)
        parts.append("Enter submit · Shift+Enter/Ctrl+J line")
        self.update(" · ".join(parts))
