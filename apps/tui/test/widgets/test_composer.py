import asyncio

from textual.app import App, ComposeResult

from apps.tui.src.widgets.composer import PersistentComposer
from apps.tui.src.submission import DraftImage, PendingMessage


class ComposerTestApp(App):
    def __init__(self) -> None:
        super().__init__()
        self.submissions = []
        self.submission_records = []
        self.image_paste_requests = 0

    def compose(self) -> ComposeResult:
        yield PersistentComposer(id="composer")

    def on_mount(self) -> None:
        self.query_one(PersistentComposer).focus()

    def on_persistent_composer_submitted(
        self, event: PersistentComposer.Submitted
    ) -> None:
        self.submissions.append(event.text)
        self.submission_records.append(event.submission)

    def on_persistent_composer_image_paste_requested(
        self, event: PersistentComposer.ImagePasteRequested
    ) -> None:
        del event
        self.image_paste_requests += 1


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


def test_attach_image在光标处插入marker并随提交发送(tmp_path):
    async def run():
        app = ComposerTestApp()
        async with app.run_test() as pilot:
            composer = app.query_one(PersistentComposer)
            composer.load_text("比较 前 后")
            composer.move_cursor((0, 3))
            image = composer.attach_image(tmp_path / "first.png")
            await pilot.pause()
            draft = (composer.text, composer.cursor_location, composer.images)
            await pilot.press("enter")
            await pilot.pause()
            return draft, image, app.submission_records, composer.has_draft

    draft, image, submissions, has_draft = asyncio.run(run())

    assert draft[0] == "比较 [#image1]前 后"
    assert draft[1] == (0, 12)
    assert draft[2] == (image,)
    assert submissions == [PendingMessage(draft[0], (image,))]
    assert has_draft is False


def test提交按marker位置排序并忽略已删除图片(tmp_path):
    async def run():
        app = ComposerTestApp()
        async with app.run_test() as pilot:
            composer = app.query_one(PersistentComposer)
            first = composer.attach_image(tmp_path / "first.png")
            second = composer.attach_image(tmp_path / "second.png")
            composer.load_text("[#image2] 重复 [#image2]，已删除第一张")
            composer.move_cursor(composer.document.end)
            await pilot.press("enter")
            await pilot.pause()
            return first, second, app.submission_records

    first, second, submissions = asyncio.run(run())

    assert first not in submissions[0].images
    assert submissions[0].images == (second,)


def test_restore_draft恢复附件并从最大编号继续(tmp_path):
    async def run():
        app = ComposerTestApp()
        async with app.run_test() as pilot:
            composer = app.query_one(PersistentComposer)
            restored = PendingMessage(
                "已有 [#image3]",
                (
                    DraftImage(
                        "image3", tmp_path / "third.png"
                    ),
                ),
            )
            composer.restore_draft(restored)
            attached = composer.attach_image(tmp_path / "fourth.png")
            await pilot.pause()
            return composer.text, composer.images, attached

    text, images, attached = asyncio.run(run())

    assert text.endswith("[#image4]")
    assert [image.reference for image in images] == ["image3", "image4"]
    assert attached.reference == "image4"


def test_ctrl_v只发布图片粘贴请求():
    async def run():
        app = ComposerTestApp()
        async with app.run_test() as pilot:
            await pilot.press("ctrl+v")
            await pilot.pause()
            return app.image_paste_requests

    assert asyncio.run(run()) == 1
