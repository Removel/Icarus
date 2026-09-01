"""Session selection modal with no Gateway or runtime responsibilities."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import ListItem, ListView, Static

from packages.gateway_protocol import SessionSummaryModel


def abbreviate_session_id(session_id: str) -> str:
    if len(session_id) <= 13:
        return session_id
    return f"{session_id[:6]}…{session_id[-4:]}"


class SessionListItem(ListItem):
    def __init__(
        self, summary: SessionSummaryModel, *, current: bool = False
    ) -> None:
        self.session_id = summary.session_id
        preview = " ".join(summary.first_user_input.split())
        super().__init__(
            Horizontal(
                Static("●" if current else "", classes="session-current"),
                Static(preview, classes="session-preview", markup=False),
                Static(
                    abbreviate_session_id(summary.session_id),
                    classes="session-short-id",
                    markup=False,
                ),
                classes="session-row",
            ),
            classes="is-current" if current else None,
        )


class SessionPicker(ModalScreen[str | None]):
    AUTO_FOCUS = "#session-list"
    BINDINGS = [Binding("escape", "cancel", show=False)]

    def __init__(
        self,
        sessions: tuple[SessionSummaryModel, ...],
        *,
        current_session_id: str | None,
    ) -> None:
        super().__init__()
        self.sessions = sessions
        self.current_session_id = current_session_id

    def compose(self) -> ComposeResult:
        with Vertical(id="session-picker-panel"):
            yield Static("Resume session", id="session-picker-title")
            if self.sessions:
                yield ListView(
                    *(
                        SessionListItem(
                            summary,
                            current=(
                                summary.session_id == self.current_session_id
                            ),
                        )
                        for summary in self.sessions
                    ),
                    id="session-list",
                )
                yield Static(
                    "↑/↓ Select · Enter Resume · Esc Cancel",
                    id="session-picker-help",
                )
            else:
                yield Static(
                    "No conversations to resume.",
                    id="session-picker-empty",
                )
                yield Static("Esc Close", id="session-picker-help")

    def on_list_view_selected(self, message: ListView.Selected) -> None:
        item = message.item
        if isinstance(item, SessionListItem):
            self.dismiss(item.session_id)

    def action_cancel(self) -> None:
        self.dismiss(None)
