import asyncio

from textual.app import App, ComposeResult

from apps.tui.src.widgets.composer import PersistentComposer


class ComposerTestApp(App):
    def __init__(self) -> None:
        super().__init__()
        self.submissions = []

    def compose(self) -> ComposeResult:
        yield PersistentComposer(id="composer")

    def on_mount(self) -> None:
        self.query_one(PersistentComposer).focus()

    def on_persistent_composer_submitted(
        self, event: PersistentComposer.Submitted
    ) -> None:
        self.submissions.append(event.text)


def run_case(keys):
    async def run():
        app = ComposerTestApp()
        async with app.run_test() as pilot:
            await pilot.press(*keys)
            await pilot.pause()
            composer = app.query_one(PersistentComposer)
            return app.submissions, composer.text, composer.cursor_location

    return asyncio.run(run())


def test_enter提交一次并清空输入框():
    submissions, text, cursor = run_case(["h", "i", "enter"])

    assert submissions == ["hi"]
    assert text == ""
    assert cursor == (0, 0)


def test_shift_enter和ctrl_j换行不提交():
    submissions, text, _ = run_case(
        ["a", "shift+enter", "b", "ctrl+j", "c"]
    )

    assert submissions == []
    assert text == "a\nb\nc"


def test多行编辑后enter保留完整内容():
    submissions, _, _ = run_case(
        ["o", "n", "e", "ctrl+j", "t", "w", "o", "up", "end", "!", "enter"]
    )

    assert submissions == ["one!\ntwo"]


def test空白内容不会提交或清空():
    submissions, text, _ = run_case(["space", "space", "enter"])

    assert submissions == []
    assert text == "  "


def test_restore_draft恢复原文并把光标放到末尾():
    async def run():
        app = ComposerTestApp()
        async with app.run_test() as pilot:
            composer = app.query_one(PersistentComposer)
            composer.restore_draft("第一行\n  second 🚀")
            await pilot.pause()
            return composer.text, composer.cursor_location, app.focused

    text, cursor, focused = asyncio.run(run())

    assert text == "第一行\n  second 🚀"
    assert cursor == (1, 10)
    assert isinstance(focused, PersistentComposer)


def test输入框从单行增长并在八行封顶():
    async def run():
        app = ComposerTestApp()
        async with app.run_test(size=(80, 24)) as pilot:
            composer = app.query_one(PersistentComposer)
            initial_height = composer.region.height
            await pilot.press(
                "1",
                "ctrl+j",
                "2",
                "ctrl+j",
                "3",
            )
            await pilot.pause()
            three_line_height = composer.region.height
            await pilot.press(
                "ctrl+j",
                "4",
                "ctrl+j",
                "5",
                "ctrl+j",
                "6",
                "ctrl+j",
                "7",
                "ctrl+j",
                "8",
                "ctrl+j",
                "9",
            )
            await pilot.pause()
            capped_height = composer.region.height
            return initial_height, three_line_height, capped_height

    initial_height, three_line_height, capped_height = asyncio.run(run())

    assert initial_height == 1
    assert three_line_height == 3
    assert capped_height == PersistentComposer.MAX_VISIBLE_LINES
