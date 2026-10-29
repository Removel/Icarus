"""Command candidates below the input; selection only, no execution logic."""

from dataclasses import dataclass

from rich.text import Text
from textual.message import Message
from textual.widgets import Static


class CommandMenu(Static):
    @dataclass
    class Chosen(Message):
        definition: object

    def __init__(self, *, id="command-menu"):
        super().__init__(id=id)
        self.items = ()
        self.selected = 0
        self.display = False

    def set_candidates(self, items, selected=0):
        self.items = tuple(items)
        self.selected = min(max(0, selected), max(0, len(self.items) - 1))
        self.refresh()

    @property
    def selection(self):
        return self.items[self.selected] if self.items else None

    def move(self, delta):
        if self.items:
            self.selected = (self.selected + delta) % len(self.items)
            self.refresh()
        if self.is_mounted:
            self.scroll_to(
                y=max(0, self.selected + 1 - max(1, self.content_size.height - 1)),
                animate=False,
                immediate=True,
            )

    def render(self):
        text = Text()
        width = self.size.width
        text.append(
            "Command        作用与描述"
            + ("                         使用方式" if width >= 90 else "")
            + "\n",
            style="dim",
        )
        if not self.items:
            text.append("没有匹配的命令\n", style="dim")
        for i, item in enumerate(self.items):
            prefix = "❯ " if i == self.selected else "  "
            text.append(
                f"{prefix}{item.name:<13}{item.summary:<36}"
                + (f"  {item.usage or item.name}" if width >= 90 else "")
                + "\n",
                style="bold #e0a8b6" if i == self.selected else "",
            )
        text.append("↑/↓ 选择 · Tab 补全 · Enter 执行 · Esc 关闭", style="dim")
        return text

    def on_click(self, event):
        event.stop()
        index = event.screen_y - self.content_region.y - 1 + int(self.scroll_y)
        if 0 <= index < len(self.items):
            self.post_message(self.Chosen(self.items[index]))
