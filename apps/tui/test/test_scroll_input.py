import asyncio
from textual.app import App
from textual.containers import Vertical, VerticalScroll
from textual import events
from apps.tui.src.widgets.chat.execution_panel import RunCard, ToolBlock, ToolRecords


def test_tool_wheel_moves_inner_without_moving_outer():
    class Host(App):
        CSS = """#outer {height:8;} RunCard {height:auto;} ToolRecords {height:auto;max-height:4;} ToolBlock {height:auto;}"""

        def compose(self):
            with VerticalScroll(id="outer"):
                yield RunCard("t")

    async def run():
        app = Host()
        async with app.run_test(size=(80, 24)) as pilot:
            card = app.query_one(RunCard)
            for i in range(15):
                await card.mount_record(ToolBlock(call_id=str(i), tool_name="read"))
            await pilot.pause()
            inner = app.query_one(ToolRecords)
            inner.scroll_home(animate=False, immediate=True)
            outer = app.query_one("#outer")
            old = outer.scroll_y
            x, y = inner.region.x + 2, inner.region.y + 1
            event = events.MouseScrollDown(
                None,
                x=x,
                y=y,
                delta_x=0,
                delta_y=1,
                button=0,
                shift=False,
                meta=False,
                ctrl=False,
                screen_x=x,
                screen_y=y,
            )
            inner.post_message(event)
            await pilot.pause()
            assert inner.scroll_y > 0
            assert outer.scroll_y == old

    asyncio.run(run())
