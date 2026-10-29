import asyncio
import threading

from textual.document._document import Selection

from apps.tui.test.support import make_app, wait_ready
from apps.tui.src.widgets.chat.composer import PersistentComposer
from apps.tui.src.services.desktop import DesktopService


def test_ctrl_c_copies_whitespace_selection_instead_of_clearing_draft():
    async def run():
        app = make_app()
        copied = []
        app.desktop = DesktopService(write_text=copied.append)
        async with app.run_test() as pilot:
            await wait_ready(pilot)
            composer = app.chat_screen.query_one(PersistentComposer)
            composer.load_text("a  b")
            composer.selection = Selection((0, 1), (0, 3))
            await pilot.press("ctrl+c")
            await pilot.pause()
            assert copied == ["  "]
            assert composer.text == "a  b"
            assert composer.get_selection_text() == "  "
    asyncio.run(run())


def test_cut_waits_for_os_success_and_failure_preserves_original():
    async def run():
        app = make_app()
        copied = []
        def write(text):
            if text == "fail":
                raise OSError("clipboard busy")
            copied.append(text)
        app.desktop = DesktopService(write_text=write)
        async with app.run_test() as pilot:
            await wait_ready(pilot)
            composer = app.chat_screen.query_one(PersistentComposer)
            composer.load_text("good text")
            composer.selection = Selection((0, 0), (0, 4))
            await pilot.press("ctrl+x")
            await pilot.pause()
            assert copied == ["good"]
            assert composer.text == " text"
            composer.action_undo()
            assert composer.text == "good text"
            composer.load_text("fail")
            composer.selection = Selection((0, 0), (0, 4))
            await pilot.press("ctrl+x")
            await pilot.pause()
            assert composer.text == "fail"
            assert composer.get_selection_text() == "fail"
    asyncio.run(run())


def test_cut_late_success_after_edit_does_not_delete_new_input():
    async def run():
        app = make_app()
        entered, release = threading.Event(), threading.Event()
        def write(text):
            entered.set()
            release.wait(3)
        app.desktop = DesktopService(write_text=write)
        async with app.run_test() as pilot:
            await wait_ready(pilot)
            composer = app.chat_screen.query_one(PersistentComposer)
            composer.load_text("old")
            composer.selection = Selection((0, 0), (0, 3))
            await pilot.press("ctrl+x")
            assert await asyncio.to_thread(entered.wait, 2)
            composer.load_text("new")
            release.set()
            await pilot.pause()
            assert composer.text == "new"
    asyncio.run(run())
