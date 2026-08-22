import asyncio

from textual.app import App, ComposeResult
from textual.widgets import Static

from apps.tui.src.widgets.queue_panel import QueuePanel, message_preview


class QueueTestApp(App):
    def compose(self) -> ComposeResult:
        yield QueuePanel(id="queue")


def test_message_preview压缩换行且不改变原消息():
    message = "  first\n    second  "

    assert message_preview(message) == "first ↵ second"
    assert message == "  first\n    second  "
    assert message_preview("abcdefghij", limit=5) == "abcd…"


def test_queue_panel按FIFO显示并在空队列隐藏():
    async def run():
        app = QueueTestApp()
        async with app.run_test() as pilot:
            panel = app.query_one(QueuePanel)
            await panel.show_pending(("first", "second\nline"))
            await pilot.pause()
            rendered = [
                str(item.render())
                for item in panel.query(".queue-item")
                if isinstance(item, Static)
            ]
            classes_when_full = set(panel.classes)
            await panel.show_pending(())
            await pilot.pause()
            return (
                rendered,
                classes_when_full,
                set(panel.classes),
                panel.items,
            )

    rendered, classes_when_full, classes_when_empty, items = asyncio.run(run())

    assert rendered == ["1. first", "2. second ↵ line"]
    assert "is-empty" not in classes_when_full
    assert "is-empty" in classes_when_empty
    assert items == ()
