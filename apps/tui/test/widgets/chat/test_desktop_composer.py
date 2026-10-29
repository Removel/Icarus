import asyncio

from textual.document._document import Selection

from apps.tui.test.widgets.chat.test_composer import ComposerTestApp
from apps.tui.src.widgets.chat.composer import PersistentComposer


def test_cut_snapshot_is_transactional_and_undoable():
    async def run():
        app = ComposerTestApp()
        async with app.run_test() as pilot:
            composer = app.query_one(PersistentComposer)
            composer.load_text("中文😀 test")
            composer.selection = Selection((0, 1), (0, 3))
            snapshot = composer.begin_cut()
            assert snapshot.text == "文😀"
            assert composer.text == "中文😀 test"
            assert composer.apply_cut(snapshot)
            assert composer.text == "中 test"
            assert not composer.apply_cut(snapshot)
            composer.action_undo()
            assert composer.text == "中文😀 test"
            composer.selection = Selection((0, 0), (0, 2))
            snapshot = composer.begin_cut()
            composer.insert("changed")
            assert not composer.apply_cut(snapshot)
            composer.selection = Selection((0, 0), (0, 2))
            snapshot = composer.begin_cut()
            composer.clear_draft()
            assert not composer.apply_cut(snapshot)
    asyncio.run(run())


def test_reloading_same_text_invalidates_pending_cut():
    async def run():
        app = ComposerTestApp()
        async with app.run_test() as pilot:
            composer = app.query_one(PersistentComposer)
            composer.load_text("same")
            composer.selection = Selection((0, 0), (0, 4))
            snapshot = composer.begin_cut()
            composer.load_text("different")
            composer.load_text("same")
            composer.selection = Selection((0, 0), (0, 4))
            assert not composer.apply_cut(snapshot)
            assert composer.text == "same"
    asyncio.run(run())


def test_cut_without_selection_never_deletes_line():
    async def run():
        app = ComposerTestApp()
        async with app.run_test() as pilot:
            composer = app.query_one(PersistentComposer)
            composer.load_text("preserve line")
            await pilot.press("ctrl+x")
            assert composer.text == "preserve line"
            assert composer.begin_cut() is None
    asyncio.run(run())


def test_moving_selection_invalidates_cut_even_without_edit():
    async def run():
        app = ComposerTestApp()
        async with app.run_test() as pilot:
            composer = app.query_one(PersistentComposer)
            composer.load_text("abcdef")
            composer.selection = Selection((0, 0), (0, 2))
            snapshot = composer.begin_cut()
            composer.selection = Selection((0, 2), (0, 4))
            assert not composer.apply_cut(snapshot)
            assert composer.text == "abcdef"
    asyncio.run(run())
