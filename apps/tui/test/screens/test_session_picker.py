import asyncio

from textual.app import App

from packages.gateway_protocol import SessionSummaryModel
from apps.tui.src.screens import SessionPicker, abbreviate_session_id
from apps.tui.src.screens.session_picker import SessionListItem


def summaries():
    return (
        SessionSummaryModel(
            session_id="1234567890abcdef",
            first_user_input="  first\nmessage   with spaces  ",
        ),
        SessionSummaryModel(
            session_id="short-id", first_user_input="second"
        ),
    )


def test_abbreviate_session_id只缩写长id():
    assert abbreviate_session_id("short-id") == "short-id"
    assert abbreviate_session_id("1234567890abcdef") == "123456…cdef"


def test_session_picker展示摘要当前标记并返回完整id():
    async def run():
        app = App()
        async with app.run_test() as pilot:
            results = []
            await app.push_screen(
                SessionPicker(
                    summaries(), current_session_id="1234567890abcdef"
                ),
                callback=results.append,
            )
            await pilot.pause()
            items = list(app.screen.query(SessionListItem))
            assert len(items) == 2
            assert items[0].has_class("is-current")
            assert items[0].session_id == "1234567890abcdef"
            assert str(
                items[0].query_one(".session-preview").render()
            ) == "first message with spaces"
            await pilot.press("down", "enter")
            await pilot.pause()
            return results[0]

    assert asyncio.run(run()) == "short-id"


def test_session_picker_escape取消且空列表可关闭():
    async def run():
        app = App()
        async with app.run_test() as pilot:
            results = []
            await app.push_screen(
                SessionPicker((), current_session_id=None),
                callback=results.append,
            )
            await pilot.pause()
            assert "No conversations" in str(
                app.screen.query_one("#session-picker-empty").render()
            )
            await pilot.press("escape")
            await pilot.pause()
            return results[0]

    assert asyncio.run(run()) is None
