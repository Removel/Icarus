"""Separate task activity from persistent Session metadata."""

from rich.text import Text
from textual.widgets import Static

from apps.tui.src.domain.chat_state import RuntimePhase


class TaskStatusLine(Static):
    def show_viewmodel(self, vm):
        label = {
            RuntimePhase.READY: "idle",
            RuntimePhase.RUNNING: "running",
            RuntimePhase.STARTING: "initializing",
            RuntimePhase.CANCELLING: "cancelling",
            RuntimePhase.SWITCHING: "switching",
            RuntimePhase.STOPPING: "stopping",
            RuntimePhase.FAILED: "failed",
        }[vm.status_kind]
        text = Text(
            f"● {label}",
            style="#75bd92" if vm.status_kind == RuntimePhase.READY else "#e0a8b6",
        )
        if not vm.show_phase:
            text = Text()
        if vm.status_text and vm.show_phase:
            text.append(f"  {vm.status_text}", style="#97979e")
        if vm.pending_count:
            text.append(f"  queued {vm.pending_count}", style="#dfbb79")
        self.update(text)


class SessionMetadataBar(Static):
    COMPONENT_CLASSES = {"metadata--key", "metadata--value"}

    def show_viewmodel(self, vm):
        key_style = self.get_component_rich_style("metadata--key")
        value_style = self.get_component_rich_style("metadata--value")
        text = Text(overflow="ellipsis", no_wrap=True)
        for key, value in (
            ("model", vm.model_label),
            ("context", vm.context_label),
            ("session", vm.session_title),
            ("uid", vm.session_id),
            ("workspace", vm.workspace_path),
        ):
            if text:
                text.append("  ")
            text.append(f"{key} ", style=key_style)
            text.append(value, style=value_style)
        self.update(text)
