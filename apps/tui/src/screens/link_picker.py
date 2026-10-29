"""Keyboard equivalent of opening a link in the current reading region."""

from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import ListItem, ListView, Static


class LinkPicker(ModalScreen[str | None]):
    BINDINGS = [Binding("escape", "cancel", show=False)]
    AUTO_FOCUS = "#session-list"

    def __init__(self, messages):
        super().__init__()
        self.messages = messages
        self.urls = None

    def compose(self):
        with Vertical(id="session-picker-panel"):
            yield Static("Open link — choose message", id="session-picker-title")
            yield ListView(*(ListItem(Static(label, markup=False)) for label, _ in self.messages), id="session-list")
            yield Static("↑/↓ Select · Enter Choose · Esc Cancel", id="session-picker-help")

    async def on_list_view_selected(self, message):
        message.stop()
        if self.urls is not None:
            self.dismiss(self.urls[message.index])
            return
        self.urls = self.messages[message.index][1]
        self.query_one("#session-picker-title", Static).update("Open link — choose URL")
        listing = self.query_one(ListView)
        await listing.clear()
        await listing.extend(ListItem(Static(url, markup=False)) for url in self.urls)
        listing.index = 0
        listing.focus()

    def action_cancel(self):
        self.dismiss(None)
