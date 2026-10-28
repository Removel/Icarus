"""Local rendered-link detection; no desktop I/O or platform probing."""

import re

from rich.style import Style as RichStyle
from textual.content import Content
from textual.message import Message
from textual.style import Style
from textual.widgets import Static

_URL = re.compile(r"https?://[^\s<>`\x00-\x1f]+", re.IGNORECASE)


def url_ranges(text):
    for match in _URL.finditer(text):
        value = match.group().rstrip('.,;:!?，。；：！？、\"\'')
        for left, right in (("(", ")"), ("[", "]"), ("（", "）")):
            while value.endswith(right) and value.count(right) > value.count(left):
                value = value[:-1]
        if value:
            yield match.start(), match.start() + len(value), value


def linkified_content(text):
    content = Content(text)
    for start, end, url in url_ranges(text):
        style = Style.from_rich_style(RichStyle(underline=True, meta={"@click": f"link({url!r})"}))
        content = content.stylize(style, start, end)
    return content


class DesktopLinkClick:
    """Defer a single click until the terminal's double-click window closes."""

    async def _on_click(self, event):
        event.stop()
        event.prevent_default()
        pending = getattr(self, "_link_click_timer", None)
        if pending is not None:
            pending.stop()
        if event.chain > 1:
            self.text_select_all()
            return
        if self.screen.get_selected_text():
            return
        async def activate():
            if self.is_attached and not self.screen.get_selected_text():
                await self.broker_event("click", event)
        self._link_click_timer = self.set_timer(self.app.CLICK_CHAIN_TIME_THRESHOLD, activate)


class LinkText(DesktopLinkClick, Static):
    class OpenLinkRequested(Message):
        def __init__(self, url):
            super().__init__()
            self.url = url

    def __init__(self, text, **kwargs):
        self.body_text = text
        super().__init__(linkified_content(text), **kwargs)

    def update(self, content="", *, layout=True):
        if isinstance(content, str):
            self.body_text = content
            content = linkified_content(content)
        return super().update(content, layout=layout)

    def link_targets(self):
        return [url for _, _, url in url_ranges(self.body_text)]

    async def action_link(self, url):
        if not self.screen.get_selected_text():
            self.post_message(self.OpenLinkRequested(url))
