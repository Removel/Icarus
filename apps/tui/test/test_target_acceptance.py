"""Target command behavior and real multi-page background delivery."""

import asyncio
from datetime import UTC, datetime

from textual.screen import Screen
from packages.gateway_protocol import RuntimeUpdateModel
from apps.tui.src.app import IcarusTextualApp
from apps.tui.src.configs.config import TuiConfig
from apps.tui.src.domain.commands import create_default_command_registry
from apps.tui.src.domain.submission import PendingMessage
from apps.tui.src.services.attachments import AttachmentsService
from apps.tui.src.widgets.chat.composer import PersistentComposer
from apps.tui.src.widgets.chat.execution_panel import ThinkingBlock
from apps.tui.test.services.chat.test_runtime import Client


def test_command_match_is_stable_subsequence():
    registry = create_default_command_registry()
    assert [c.name for c in registry.match("rs")] == ["/resume"]
    assert [c.name for c in registry.match("RE")] == ["/resume"]
    assert [c.name for c in registry.match("e")] == ["/clear", "/resume", "/exit"]
    assert registry.match("zz") == ()


def test_background_update_preserves_original_composer_and_facts(tmp_path):
    async def run():
        client = Client()

        async def factory(s, c):
            return client

        app = IcarusTextualApp(
            TuiConfig(tmp_path),
            client_factory=factory,
            attachments=AttachmentsService(None),
        )
        async with app.run_test(size=(100, 32)) as pilot:
            await pilot.pause()
            composer = app.chat_screen.query_one(PersistentComposer)
            composer.load_text("retained")
            original_children = len(app.chat_screen.query("*"))
            await app.push_screen(Screen())
            for index in range(5):
                client.subscription.publish(
                    RuntimeUpdateModel(
                        workspace_key="w",
                        session_id="s",
                        task_id="t",
                        type="user.message",
                        payload={"text": f"background {index}", "resources": []},
                        sequence=index + 1,
                        occurred_at=datetime.now(UTC),
                    )
                )
            for _ in range(50):
                if len(app.chat_store.conversation.turns) == 5:
                    break
                await asyncio.sleep(0.005)
            assert len(app.chat_store.conversation.turns) == 5
            assert len(app.chat_screen.query("*")) == original_children
            await app.pop_screen()
            await pilot.pause()
            assert app.chat_screen.query_one(PersistentComposer) is composer
            assert composer.text == "retained"
            assert app.chat_screen.rendered_revision == app.chat_store.revision
            app.request_shutdown()

    asyncio.run(run())


def test_completed_thinking_timer_paused(tmp_path):
    from textual.app import App

    class Host(App):
        def compose(self):
            yield ThinkingBlock(step=1)

    async def run():
        app = Host()
        async with app.run_test() as pilot:
            thinking = app.query_one(ThinkingBlock)
            await thinking.complete_text("done", partial=False)
            before = thinking._spin_frame
            await asyncio.sleep(0.25)
            assert thinking._spin_frame == before
            assert thinking.completed

    asyncio.run(run())
