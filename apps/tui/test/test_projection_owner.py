import asyncio
from pathlib import Path
from textual.app import App
from apps.tui.src.widgets.chat.conversation import ConversationView
from apps.tui.src.stores.chat import ChatStore
from apps.tui.src.event_pipeline import AppendUserMessage, AppendThinkingDelta


def test_external_projection_is_not_reset_or_reapplied_by_renderer(tmp_path):
    store = ChatStore()
    store.apply_actions(
        (AppendUserMessage("t", "hello"), AppendThinkingDelta("t", 1, "first"))
    )

    class Host(App):
        def compose(self):
            yield ConversationView(
                tmp_path, projection=store.conversation, id="conversation"
            )

    async def run():
        app = Host()
        async with app.run_test() as pilot:
            view = app.query_one(ConversationView)
            await view.reconcile(store.capture_window(0, 1))
            assert len(store.conversation.turns) == 1
            assert store.conversation.units[1].children[0].text == "first"

    asyncio.run(run())
