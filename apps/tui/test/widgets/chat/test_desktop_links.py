import asyncio

from textual.app import App
from textual.widgets import Markdown

from apps.tui.src.widgets.chat.markdown import StreamingMarkdown
from apps.tui.src.widgets.chat.message_cards import UserMessage


def test_rendered_links_include_bare_urls_but_exclude_inline_and_fenced_code():
    async def run():
        class LinkApp(App):
            def compose(self):
                yield StreamingMarkdown('正文 https://example.com/路径。 `https://code.test`\n\n[声明](https://target.test/x)\n\n```text\nhttps://fence.test\n```')
                yield UserMessage('用户 https://user.test/x?q=1')
        app = LinkApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            markdown = app.query_one(StreamingMarkdown)
            assert markdown.link_targets() == ['https://example.com/路径', 'https://target.test/x']
            assert app.query_one(UserMessage).link_targets() == ['https://user.test/x?q=1']
    asyncio.run(run())


def test_expanded_tool_body_keeps_link_spans_after_completion():
    from apps.tui.src.widgets.chat.execution_panel import ToolBlock
    from apps.tui.src.widgets.chat.links import LinkText
    async def run():
        class ToolApp(App):
            def compose(self):
                yield ToolBlock(call_id='t', tool_name='search', arguments_json='{}')
        app = ToolApp()
        async with app.run_test() as pilot:
            tool = app.query_one(ToolBlock)
            tool.set_expanded(True)
            tool.complete(success=True, output_preview='see https://result.test/page')
            await pilot.pause()
            body = tool.query_one('.tool-details', LinkText)
            assert body.link_targets() == ['https://result.test/page']
            assert any(getattr(span.style, 'meta', {}).get('@click') for span in body.render().spans)
    asyncio.run(run())


def test_markdown_link_posts_intent_instead_of_direct_browser_call():
    async def run():
        class LinkApp(App):
            def __init__(self):
                super().__init__()
                self.opened = []
                self.requested = []
            def compose(self):
                yield StreamingMarkdown('[link](https://example.com)')
            def open_url(self, url):
                self.opened.append(url)
            def on_streaming_markdown_open_link_requested(self, message):
                self.requested.append(message.url)
        app = LinkApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            markdown = app.query_one(StreamingMarkdown)
            markdown.post_message(Markdown.LinkClicked(markdown, 'https://example.com'))
            await pilot.pause()
            assert app.opened == []
            assert app.requested == ['https://example.com']
    asyncio.run(run())
