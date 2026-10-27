"""Capture real Textual rendering for approved-demo visual review (no model calls)."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from apps.tui.src.app import IcarusTextualApp
from apps.tui.src.configs.config import TuiConfig
from apps.tui.src.domain.chat_state import RuntimePhase
from apps.tui.src.services.attachments import AttachmentsService
from apps.tui.src.tools.replay import ReplayRuntimeService
from apps.tui.src.widgets.chat.composer import PersistentComposer
from apps.tui.src.widgets.chat.execution_panel import ToolBlock


async def capture(out: Path, width: int, height: int):
    root = Path(__file__).resolve().parents[1]
    from apps.tui.src.tools.preview import preview_scenario

    scenario = preview_scenario()
    service = ReplayRuntimeService(
        scenario, events_per_second=50, session_id="preview-session"
    )

    async def factory(session_id, create):
        return service

    app = IcarusTextualApp(
        TuiConfig(workspace_path=Path.cwd()),
        client_factory=factory,
        attachments=AttachmentsService(None),
    )
    async with app.run_test(size=(width, height)) as pilot:
        await pilot.pause()
        composer = app.chat_screen.query_one(PersistentComposer)
        for node_id in ("composer-shell", "task-status", "session-metadata"):
            node = app.chat_screen.query_one("#" + node_id)
            assert node.region.bottom <= height, (node_id, node.region, height)
        app.save_screenshot("welcome.svg", path=str(out))
        await pilot.press(*"检查配置并整理 TUI", "enter")
        for _ in range(200):
            await pilot.pause()
            if (
                service.submissions
                and app.chat_store.interaction.phase == RuntimePhase.READY
            ):
                break
        await pilot.pause()
        if not service.submissions or app.chat_store.fatal_message:
            raise RuntimeError(app.chat_store.fatal_message or "Replay did not start")
        app.chat_screen.action_latest()
        await pilot.pause()
        app.save_screenshot("chat.svg", path=str(out))
        thinking = next(
            iter(app.chat_screen.query(".current-thinking ThinkingBlock")), None
        )
        if thinking:
            thinking.set_expanded(True)
        tool = next(iter(app.chat_screen.query(ToolBlock)), None)
        if tool:
            tool.set_expanded(True)
        await pilot.pause()
        app.save_screenshot("details.svg", path=str(out))
        app.chat_screen.query_one(PersistentComposer).focus()
        await pilot.press("/")
        await pilot.pause()
        assert app.chat_screen.query_one("#command-menu").display
        assert app.chat_screen.query_one("#session-metadata").region.bottom <= height
        app.save_screenshot("commands.svg", path=str(out))
        app.request_shutdown(return_code=0)
        await pilot.pause()
    print(
        f"Real Textual frames captured: {out}; submitted={len(service.submissions)}; phase={app.chat_store.interaction.phase.value}"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--width", type=int, default=120)
    parser.add_argument("--height", type=int, default=40)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    asyncio.run(capture(args.out, args.width, args.height))


if __name__ == "__main__":
    main()
