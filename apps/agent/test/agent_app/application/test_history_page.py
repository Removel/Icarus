import asyncio

from agent_app.application import AgentRuntime
from apps.agent.test.agent_app.application.test_agent_runtime import RuntimeFactory, make_config


def test_history_paging_preserves_legacy_interface_and_cursor(tmp_path):
    async def run():
        runtime = AgentRuntime(config_loader=lambda: make_config(tmp_path / "data"), session_factory=RuntimeFactory())
        assert hasattr(runtime, "get_session_history_page"), "application paging entry point is missing"
        await runtime.start()
        try:
            session = await runtime.create_session(tmp_path, "paging")
            await runtime.submit(tmp_path, session, "first", submission_id="a")
            await runtime.submit(tmp_path, session, "second", submission_id="b")
            records, cursor = await runtime.get_session_history(tmp_path, session)
            page = await runtime.get_session_history_page(tmp_path, session, limit=1)
            assert page.records == records[:1]
            assert page.history_cursor == cursor
            assert page.next_after_sequence == records[0].sequence
            assert page.has_more is True
            empty = await runtime.get_session_history_page(tmp_path, session, after_sequence=cursor)
            assert empty.records == ()
            assert empty.next_after_sequence == cursor
            assert empty.has_more is False
        finally:
            await runtime.stop()
    asyncio.run(run())
