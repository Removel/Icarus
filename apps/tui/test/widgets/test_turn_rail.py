import asyncio

from pathlib import Path

from textual.app import App, ComposeResult

from textual.containers import Horizontal, Vertical

from textual.widgets import Static

from apps.tui.src.widgets.turn_rail import TurnRail


class RailTestApp(App):
    CSS = "#turn-rail { height: 1fr; width: 2; }"

    def compose(self) -> ComposeResult:
        with Vertical():
            yield TurnRail(id="turn-rail")


class RailShellApp(App):
    """The rail inside the real conversation shell, so styles.tcss applies."""

    CSS_PATH = Path(__file__).resolve().parents[2] / "src" / "styles.tcss"

    def compose(self) -> ComposeResult:
        with Horizontal(id="conversation-shell"):
            yield Static("", id="conversation")
            yield TurnRail(id="turn-rail")


def test_turn_rail仅显示十个无编号圆点且悬停显示摘要():
    async def run():
        app = RailTestApp()
        async with app.run_test(size=(80, 32)) as pilot:
            rail = app.query_one(TurnRail)
            rail.set_turns([f"question {i}" for i in range(302)], current=301)
            await pilot.pause()
            before = rail.renderable.plain
            rail.show_excerpts()
            await pilot.pause()
            after = rail.excerpts.plain
            return before, after

    before, after = asyncio.run(run())
    assert before.count("○") + before.count("●") == 10
    assert "292" not in before
    assert "302" not in before
    assert "question 301" in after
    assert "question 292" in after
    assert "302" not in after


def test_turn_rail滚轮只移动目录窗口不跳转正文():
    async def run():
        app = RailTestApp()
        async with app.run_test(size=(80, 32)) as pilot:
            rail = app.query_one(TurnRail)
            rail.set_turns([f"question {i}" for i in range(1000)], current=999)
            await pilot.pause()
            before = rail.window_start
            rail.scroll_turns(-1)
            await pilot.pause()
            return before, rail.window_start, rail.current

    assert asyncio.run(run()) == (990, 989, 999)


def test_turn_rail_键盘在选中轮次后跳转():
    async def run():
        app = RailTestApp()
        async with app.run_test(size=(80, 32)) as pilot:
            rail = app.query_one(TurnRail)
            rail.set_turns([f"question {i}" for i in range(30)], current=29)
            rail.focus()
            await pilot.press("up", "enter")
            await pilot.pause()
            return rail.selected_turn

    assert asyncio.run(run()) == 28


def test_turn_rail_mouse_click_selects_corresponding_dot():
    async def run():
        app = RailTestApp()
        async with app.run_test(size=(80, 32)) as pilot:
            rail = app.query_one(TurnRail)
            rail.set_turns([f"question {i}" for i in range(30)], current=29)
            await pilot.pause()
            rail.on_click(type("Click", (), {"y": 8, "stop": lambda self: None})())
            await pilot.pause()
            return rail.selected_turn

    assert asyncio.run(run()) == 21


def test_turn_rail_focused_summary_panel_click_selects_row():
    async def run():
        app = RailTestApp()
        async with app.run_test(size=(80, 32)) as pilot:
            rail = app.query_one(TurnRail)
            rail.set_turns([f"question {i}" for i in range(30)], current=29)
            rail.show_excerpts()
            await pilot.pause()
            panel = rail.query_one("#turn-rail-excerpts")
            panel.on_click(type("Click", (), {"y": 2, "stop": lambda self: None})())
            await pilot.pause()
            return rail.selected_turn

    # Row 2 is the spacer row under the first excerpt, so it selects window_start.
    assert asyncio.run(run()) == 20


def test_turn_rail_excerpt_click_selects_the_summary_under_the_pointer():
    async def run():
        app = RailShellApp()
        async with app.run_test(size=(100, 32)) as pilot:
            rail = app.query_one(TurnRail)
            rail.set_turns([f"question {i}" for i in range(30)], current=29)
            rail.show_excerpts()
            await pilot.pause()
            panel = rail.query_one("#turn-rail-excerpts")
            selected = []
            for item in (0, 2, 5, 9):
                rail.selected_turn = None
                if not panel.display:
                    rail.show_excerpts()
                    await pilot.pause()
                # Each excerpt paints its dot row plus a blank spacer row.
                row = panel.region.y + 1 + item * 2
                await pilot.click(offset=(panel.region.x + 4, row))
                await pilot.pause()
                selected.append(rail.selected_turn)
            return rail.window_start, selected

    window_start, selected = asyncio.run(run())
    assert window_start == 20
    assert selected == [20, 22, 25, 29]


def test_turn_rail_excerpt_list_aligns_with_centered_dots():
    from apps.tui.test.test_app_snapshots import SnapshotService, make_app, wait_ready

    async def run():
        app = make_app(SnapshotService())
        async with app.run_test(size=(100, 32)) as pilot:
            await wait_ready(pilot)
            rail = app.query_one(TurnRail)
            rail.set_turns([f"question {i}" for i in range(30)], current=29)
            rail.show_excerpts()
            await pilot.pause()
            panel = rail.query_one("#turn-rail-excerpts")
            return panel.region.y, rail.region.y, panel.region.height, rail.region.height

    top, rail_top, height, rail_height = asyncio.run(run())
    # Hovering the first dot should align the first excerpt, not center the list.
    first_dot_y = rail_top + (rail_height - 20) // 2
    assert abs((top + 1) - first_dot_y) <= 1


def test_turn_rail_short_excerpt_list_starts_at_first_dot_on_hover():
    from textual.geometry import Offset

    from apps.tui.test.test_app_snapshots import SnapshotService, make_app, wait_ready

    async def run():
        app = make_app(SnapshotService())
        async with app.run_test(size=(80, 18)) as pilot:
            await wait_ready(pilot)
            rail = app.query_one(TurnRail)
            rail.set_turns(["first", "second", "third", "fourth"], current=3)
            await pilot.hover("#turn-rail-dots", offset=Offset(0, 2))
            await pilot.pause()
            panel = rail.query_one("#turn-rail-excerpts")
            first_dot = rail.region.y + (rail.size.height - 8) // 2
            return first_dot, panel.region.y + 1

    assert asyncio.run(run()) == (4, 4)


def test_turn_rail_excerpt_cjk_rows_keep_dot_before_text():
    async def run():
        app = RailTestApp()
        async with app.run_test(size=(80, 32)) as pilot:
            rail = app.query_one(TurnRail)
            rail.set_turns(["能帮我去GitHub上继续", "帮我启动snowluma"], current=1)
            rail.show_excerpts()
            await pilot.pause()
            return rail.excerpts.plain.splitlines()

    rows = asyncio.run(run())
    assert len(rows) == 3
    assert rows[0].startswith("○ 能帮我去")
    assert rows[1] == ""
    assert rows[2].startswith("● 帮我启动")
    assert all(not row or row[0] in "○●" for row in rows)


def test_turn_rail_resize_reduces_visible_dots():
    async def run():
        app = RailTestApp()
        async with app.run_test(size=(80, 32)) as pilot:
            rail = app.query_one(TurnRail)
            rail.set_turns([f"question {i}" for i in range(30)], current=29)
            await pilot.pause()
            assert rail.renderable.plain.count("○") + rail.renderable.plain.count("●") == 10
            await pilot.resize_terminal(80, 10)
            await pilot.pause()
            return rail.renderable.plain.count("○") + rail.renderable.plain.count("●"), rail.capacity

    dots, capacity = asyncio.run(run())
    assert dots == capacity


def test_turn_rail_keeps_keyboard_cursor_when_turns_refresh():
    async def run():
        app = RailTestApp()
        async with app.run_test(size=(80, 32)) as pilot:
            rail = app.query_one(TurnRail)
            rail.set_turns(["one", "two", "three"], current=2)
            rail.focus()
            await pilot.press("up")
            rail.set_turns(["one", "two", "three"], current=2)
            await pilot.press("enter")
            await pilot.pause()
            return rail.selected_turn

    assert asyncio.run(run()) == 1


def test_turn_rail_ignores_popup_border_click():
    async def run():
        app = RailTestApp()
        async with app.run_test(size=(80, 32)) as pilot:
            rail = app.query_one(TurnRail)
            rail.set_turns([f"question {i}" for i in range(30)], current=29)
            rail.show_excerpts()
            await pilot.pause()
            panel = rail.query_one("#turn-rail-excerpts")
            panel.on_click(type("Click", (), {"y": 0, "stop": lambda self: None})())
            await pilot.pause()
            return rail.selected_turn

    assert asyncio.run(run()) is None
