import asyncio
from textual.app import App
from apps.tui.src.widgets.chat.execution_panel import RunCard, ToolBlock


def test_tool_execution_region_caps_height_and_scrolls(tmp_path):
    class Host(App):
        CSS = "RunCard {height:auto;} .tool-records {max-height:8; height:auto;} ToolBlock {height:auto;}"

        def compose(self):
            yield RunCard("t")

    async def run():
        app = Host()
        async with app.run_test(size=(80, 24)) as pilot:
            card = app.query_one(RunCard)
            for i in range(16):
                await card.mount_record(ToolBlock(call_id=str(i), tool_name="read"))
            await pilot.pause()
            records = card.query_one(".tool-records")
            assert records.region.height <= 8
            assert records.max_scroll_y > 0

    asyncio.run(run())
