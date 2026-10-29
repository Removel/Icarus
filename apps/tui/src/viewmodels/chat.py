"""Pure, read-only display values derived from the chat Store."""

from dataclasses import dataclass

from apps.tui.src.domain.chat_state import RuntimePhase
from apps.tui.src.stores.chat import ChatStore


@dataclass(frozen=True)
class ChatViewModel:
    status_text: str
    status_kind: RuntimePhase
    pending_count: int
    can_run_session_command: bool
    show_phase: bool
    session_title: str
    session_id: str
    workspace_path: str
    model_label: str
    reasoning_label: str
    context_label: str
    version_label: str
    latest_version_label: str = "未检查"


def build_chat_viewmodel(store: ChatStore) -> ChatViewModel:
    state = store.interaction
    return ChatViewModel(
        status_text=store.fatal_message or store.status_message,
        status_kind=state.phase,
        pending_count=len(state.pending),
        can_run_session_command=state.can_run_session_command,
        show_phase=store.has_user_input or state.phase == RuntimePhase.STOPPING,
        session_title=store.session_title,
        session_id=store.session_id or "—",
        workspace_path=store.workspace_path,
        model_label=store.model_label or "未知",
        reasoning_label=store.reasoning_label or "未知",
        context_label=store.context_label or "—",
        version_label=store.app_version or "未知",
    )
