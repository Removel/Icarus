"""Message identity stays in the header, not a full-height color rail."""

import asyncio

import pytest
from textual.widgets import Label

from apps.tui.src.event_pipeline import (
    AppendUserMessage,
    AppendUserCorrection,
    CompleteAssistantMessage,
)
from apps.tui.src.widgets.chat.message_cards import (
    AssistantMessage,
    UserCorrectionBlock,
    UserMessage,
)
from apps.tui.test.widgets.chat.test_execution_message_groups import app_for


@pytest.mark.parametrize("size", [(120, 40), (70, 24), (100, 12)])
def test_long_messages_use_role_headers_without_full_height_rails(tmp_path, size):
    async def run():
        app = app_for(tmp_path)
        async with app.run_test(size=size) as pilot:
            await pilot.pause()
            text = "\n\n".join(f"Long paragraph {i}: readable without a color rail." for i in range(20))
            app.chat_store.apply_actions((
                AppendUserMessage("t", "Please inspect the configuration."),
                CompleteAssistantMessage("t", text, 1),
                AppendUserCorrection("t", "Also check defaults.", 2),
            ))
            await app.chat_screen.refresh_chat()
            await pilot.pause()
            user = app.chat_screen.query_one(UserMessage)
            assistant = app.chat_screen.query_one(AssistantMessage)
            correction = app.chat_screen.query_one(UserCorrectionBlock)
            for message in (user, assistant, correction):
                assert message.styles.border_left[0] in {"", "none"}
            assert str(user.query_one(Label).render()) == "❯ You"
            assert str(assistant.query_one(Label).render()) == "✦ Icarus"
            assert str(correction.query_one(".correction-label").render()).startswith("❯ ")
            assert assistant.markdown_text == text
            assert assistant.region.height > 20
            assert user.query_one(".message-body-scroll").styles.background.a > 0
            assert correction.query_one(".correction-content").styles.background.a > 0
            app.request_shutdown()

    asyncio.run(run())
