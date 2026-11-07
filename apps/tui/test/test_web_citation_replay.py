"""Exercise citation metadata in the real offline Textual application."""

import asyncio
import os
from pathlib import Path

import pytest
from textual.widgets._markdown import MarkdownParagraph

from apps.tui.src.app import IcarusTextualApp
from apps.tui.src.configs.config import TuiConfig
from apps.tui.src.domain.chat_state import RuntimePhase
from apps.tui.src.services.attachments import AttachmentsService
from apps.tui.src.tools.replay import ReplayRuntimeService, load_replay
from apps.tui.src.tools.transcript import transcript_from_scenario
from apps.tui.src.widgets.chat.message_cards import AssistantMessage


@pytest.mark.parametrize("size", [(120, 40), (70, 24), (100, 12)])
def test_real_shell_citation_replay(tmp_path, size):
    scenario = load_replay(Path(__file__).parent / "fixtures/web_citations.jsonl")
    assert "联网结论[1]" in transcript_from_scenario(scenario)  # Raw debug replay remains intact.

    async def run():
        service = ReplayRuntimeService(scenario, events_per_second=100, session_id="replay-session")

        async def factory(session_id, create):
            return service

        app = IcarusTextualApp(TuiConfig(workspace_path=tmp_path),
            client_factory=factory, attachments=AttachmentsService(None))
        async with app.run_test(size=size) as pilot:
            await pilot.pause()
            await pilot.press(*"search", "enter")
            for _ in range(100):
                await pilot.pause()
                if service.submissions and app.chat_store.interaction.phase == RuntimePhase.READY:
                    break
            assert service.submissions and not app.chat_store.fatal_message
            app.chat_screen.action_latest()
            await pilot.pause()
            assistant = app.chat_screen.query_one(AssistantMessage)
            text = "".join(node._content.plain for node in assistant.query(MarkdownParagraph))
            assert text == "联网结论，代码 data[1] 保留。普通编号[9]。"
            assert "联网结论[1]" in assistant.markdown_text
            directory = os.environ.get("TUI_CAPTURE_DIR")
            if directory:
                Path(directory).mkdir(parents=True, exist_ok=True)
                app.save_screenshot(f"citations-{size[0]}x{size[1]}.svg", path=directory)
            app.request_shutdown(return_code=0)
            await pilot.pause()
    asyncio.run(run())
