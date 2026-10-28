"""Compact user-input turn navigation for the conversation shell."""

from __future__ import annotations

from collections.abc import Sequence

from rich.cells import cell_len, chop_cells
from rich.text import Text

from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.message import Message
from textual.widgets import Static

from apps.tui.src.widgets.queue_panel import message_preview


class TurnExcerpts(Static):
    """Clickable summary list for the rail's visible turns."""

    def on_click(self, event: events.Click) -> None:
        event.stop()
        rail = self.parent
        if isinstance(rail, TurnRail):
            # Each excerpt paints a dot row plus a blank spacer row.
            row = (event.y - 1) // 2
            index = rail.window_start + row
            if 0 <= row < min(rail.capacity, len(rail._turns) - rail.window_start):
                rail._cursor = index
                rail.action_select()


class TurnRail(Static):
    """Ten visible turn markers; scrolling changes the rail, not the chat."""

    can_focus = True
    BINDINGS = [
        Binding("up", "previous", show=False),
        Binding("down", "next", show=False),
        Binding("pageup", "older", show=False),
        Binding("pagedown", "newer", show=False),
        Binding("home", "first", show=False),
        Binding("end", "last", show=False),
        Binding("enter", "select", show=False),
    ]

    class TurnSelected(Message):
        def __init__(self, index: int) -> None:
            self.index = index
            super().__init__()

    def __init__(self, *, id: str | None = None) -> None:
        super().__init__(id=id)
        self._turns: tuple[str, ...] = ()
        self._dots_text = ""
        self._excerpts_text = ""
        self.window_start = 0
        self.current = 0
        self._cursor = 0
        self.selected_turn: int | None = None
        self._excerpts_open = False

    def compose(self) -> ComposeResult:
        yield Static("", id="turn-rail-dots", markup=False)
        yield TurnExcerpts("", id="turn-rail-excerpts", markup=False)

    @property
    def renderable(self):
        return Text(self._dots_text)

    @property
    def excerpts(self):
        return Text(self._excerpts_text)

    @property
    def capacity(self) -> int:
        return max(1, min(10, self.size.height // 2))

    def on_resize(self, event: events.Resize) -> None:
        self.window_start = min(self.window_start, max(0, len(self._turns) - self.capacity))
        self._refresh()

    def set_turns(self, turns: Sequence[str], current: int) -> None:
        previous_count = len(self._turns)
        previous_start = self.window_start
        was_at_latest = previous_start >= max(0, previous_count - self.capacity)
        same_turns = tuple(turns) == self._turns
        previous_cursor = self._cursor
        self._turns = tuple(turns)
        if not self._turns:
            self.current = self._cursor = self.window_start = 0
        else:
            self.current = max(0, min(current, len(self._turns) - 1))
            self._cursor = previous_cursor if same_turns and self.has_focus else self.current
            self.window_start = (
                max(0, len(self._turns) - self.capacity)
                if was_at_latest else min(previous_start, len(self._turns) - 1)
            )
        self._refresh()

    @staticmethod
    def _excerpt(text: str) -> str:
        compact = message_preview(text, limit=200)
        if cell_len(compact) <= 15:
            return compact
        return chop_cells(compact, 14)[0].rstrip() + "…"

    def _refresh(self) -> None:
        visible = range(
            self.window_start,
            min(len(self._turns), self.window_start + self.capacity),
        )
        dots = "\n".join(
            ("●" if index == self.current else "○") + "\n"
            for index in visible
        )
        excerpts = "\n".join(
            f"{'●' if index == self.current else '○'} "
            f"{self._excerpt(self._turns[index])}\n"
            for index in visible
        )
        self._dots_text = dots
        self._excerpts_text = excerpts
        if self.is_mounted:
            self.query_one("#turn-rail-dots", Static).update(dots)
            panel = self.query_one("#turn-rail-excerpts", Static)
            panel.update(excerpts)
            panel.display = self._excerpts_open and bool(self._turns)
            if panel.display:
                self.call_after_refresh(self._position_excerpts)

    def scroll_turns(self, direction: int) -> None:
        self.window_start = max(
            0,
            min(
                max(0, len(self._turns) - self.capacity),
                self.window_start + direction,
            ),
        )
        self._refresh()

    def _position_excerpts(self) -> None:
        if not self.is_mounted:
            return
        panel = self.query_one("#turn-rail-excerpts", Static)
        if panel.display:
            visible = min(self.capacity, len(self._turns) - self.window_start)
            first_dot = max(0, (self.size.height - visible * 2) // 2)
            panel.styles.offset = (-20, max(0, first_dot - 1))

    def show_excerpts(self) -> None:
        self._excerpts_open = True
        self._refresh()
        self.call_after_refresh(self._position_excerpts)

    def hide_excerpts(self) -> None:
        self._excerpts_open = False
        self._refresh()

    def on_enter(self, event: events.Enter) -> None:
        self.show_excerpts()

    def on_leave(self, event: events.Leave) -> None:
        self.hide_excerpts()

    def on_mouse_scroll_up(self, event: events.MouseScrollUp) -> None:
        event.stop()
        self.scroll_turns(-1)

    def on_mouse_scroll_down(self, event: events.MouseScrollDown) -> None:
        event.stop()
        self.scroll_turns(1)

    def on_click(self, event: events.Click) -> None:
        event.stop()
        visible = min(self.capacity, len(self._turns) - self.window_start)
        first_row = max(0, (self.size.height - visible * 2) // 2)
        row = (event.y - first_row) // 2
        index = self.window_start + row
        if 0 <= row < visible and index < len(self._turns):
            self._cursor = index
            self.action_select()

    def action_previous(self) -> None:
        self._move_cursor(-1)

    def action_next(self) -> None:
        self._move_cursor(1)

    def action_older(self) -> None:
        self._move_cursor(-self.capacity)

    def action_newer(self) -> None:
        self._move_cursor(self.capacity)

    def action_first(self) -> None:
        self._move_cursor(-len(self._turns))

    def action_last(self) -> None:
        self._move_cursor(len(self._turns))

    def _move_cursor(self, amount: int) -> None:
        if not self._turns:
            return
        self._cursor = max(0, min(len(self._turns) - 1, self._cursor + amount))
        if self._cursor < self.window_start:
            self.window_start = self._cursor
        elif self._cursor >= self.window_start + self.capacity:
            self.window_start = self._cursor - self.capacity + 1
        self._refresh()

    def action_select(self) -> None:
        if self._turns:
            self.selected_turn = self._cursor
            self.post_message(self.TurnSelected(self._cursor))
