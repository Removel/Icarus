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
            text = Text("Enter submit · / commands", style="#97979e")
        if vm.status_text and vm.show_phase:
            text.append(f"  {vm.status_text}", style="#97979e")
        if vm.pending_count:
            text.append(f"  queued {vm.pending_count}", style="#dfbb79")
        self.update(text)


class SessionMetadataBar(Static):
    def show_viewmodel(self, vm):
        self.update(
            Text(
                f"模型 {vm.model_label}   思考 {vm.reasoning_label}   上下文 {vm.context_label}   会话 {vm.session_title}   uid {vm.session_id}   工作区 {vm.workspace_path}",
                style="#97979e",
                overflow="ellipsis",
                no_wrap=True,
            )
        )
