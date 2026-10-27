"""Persistent bottom composition and slash completion; no client dependencies."""

from textual.containers import Horizontal, Vertical
from textual.widgets import Static

from apps.tui.src.widgets.chat.command_menu import CommandMenu
from apps.tui.src.widgets.chat.composer import PersistentComposer
from apps.tui.src.widgets.chat.queue_panel import QueuePanel
from apps.tui.src.widgets.chat.status import SessionMetadataBar, TaskStatusLine


class InputDock(Vertical):
    def __init__(self, commands, *, id="input-dock"):
        super().__init__(id=id)
        self.commands = commands
        self._dismissed = False
        self._last_text = ""

    def compose(self):
        yield TaskStatusLine(id="task-status")
        yield QueuePanel(id="queue-panel")
        with Horizontal(id="composer-shell"):
            yield Static("❯", id="composer-prompt")
            yield PersistentComposer(id="composer")
        yield CommandMenu()
        yield SessionMetadataBar(id="session-metadata")

    def on_mount(self):
        self.query_one(PersistentComposer).key_handler = self._command_key
        self._budget()

    def on_resize(self):
        self._budget()

    def _budget(self):
        if not self.is_mounted:
            return
        composer = self.query_one(PersistentComposer)
        screen_height = self.screen.size.height
        queue_rows = 3 if self.query_one(QueuePanel).items else 0
        menu_rows = (
            min(5, len(self.query_one(CommandMenu).items) + 2)
            if self.query_one(CommandMenu).display
            else 0
        )
        composer.MAX_VISIBLE_LINES = min(
            8, max(1, screen_height - 3 - 2 - 2 - queue_rows - menu_rows)
        )
        composer._sync_height()

    def on_text_area_changed(self, event):
        composer = self.query_one(PersistentComposer)
        if event.text_area is not composer:
            return
        if composer.text != self._last_text:
            self._dismissed = False
            self._last_text = composer.text
        self._update_menu()
        self._budget()

    def on_text_area_selection_changed(self):
        self._update_menu()

    def _update_menu(self):
        composer = self.query_one(PersistentComposer)
        menu = self.query_one(CommandMenu)
        raw = composer.text.lstrip()
        eligible = (
            raw.startswith("/")
            and "\n" not in raw
            and not any(ch.isspace() for ch in raw)
            and composer.cursor_location == composer.document.end
        )
        menu.display = eligible and not self._dismissed
        if menu.display:
            items = self.commands.match(raw)
            menu.set_candidates(items, menu.selected if items == menu.items else 0)

    def _command_key(self, event):
        menu = self.query_one(CommandMenu)
        composer = self.query_one(PersistentComposer)
        if not menu.display:
            return False
        if event.key == "escape":
            self._dismissed = True
            menu.display = False
            return True
        if event.key in {"up", "down"} and menu.items:
            menu.move(1 if event.key == "down" else -1)
            return True
        if event.key in {"tab", "enter"} and menu.selection:
            composer.load_text(menu.selection.name)
            composer.move_cursor(composer.document.end)
            if event.key == "enter":
                composer.submit()
            return True
        if event.key in {"shift+enter", "ctrl+j", "newline"}:
            menu.display = False
        return False

    def on_command_menu_chosen(self, message):
        message.stop()
        composer = self.query_one(PersistentComposer)
        composer.load_text(message.definition.name)
        composer.submit()
        composer.focus()

    async def show_viewmodel(self, vm, pending):
        self.query_one(TaskStatusLine).show_viewmodel(vm)
        self.query_one(SessionMetadataBar).show_viewmodel(vm)
        await self.query_one(QueuePanel).show_pending(pending)
        self._budget()
