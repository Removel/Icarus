import asyncio

import pytest

from agent_app.application.session_runtime import SessionRuntime
from agent_app.plugins.persistence import SessionIdentity
from agent_app.plugins.memory.models import MemoryRecallResult
from icarus.core.agents import AgentCompletedEvent, AgentMessageCompletedEvent, AgentResponse
from icarus.core.models.messages import Message, TextPart, Usage
from apps.agent.test.agent_app.application.test_session_runtime import make_config
from apps.agent.test.agent_app.plugins.web_search.test_pipeline import Backend


@pytest.mark.parametrize("mode", ["off", "skip", "on", "search-failed", "fetch-failed", "cancel"])
def test_search_uses_real_runtime_events_and_never_changes_system_prompt(tmp_path, monkeypatch, mode):
    async def empty_recall(self, query, **kwargs):
        return MemoryRecallResult((), query)
    monkeypatch.setattr("agent_app.plugins.memory.mem0_http_adapter.Mem0HttpAdapter.arecall", empty_recall)

    async def run():
        updates, captured = [], []
        async def publish(update):
            updates.append(update)
        runtime = SessionRuntime(SessionIdentity.create(tmp_path, "search-test"),
            config=make_config(tmp_path / "data"), publish_update=publish)
        await runtime.start()
        search = runtime.runtime_host.get_plugin("web-search")
        await search.backend.aclose()
        search.backend = Backend(searches_fail={"alpha", "beta"} if mode == "search-failed" else (),
            fetches_fail={"all"} if mode == "fetch-failed" else ())
        entered = asyncio.Event()
        stopped = []
        async def planner(question, clock):
            if mode == "off":
                raise AssertionError("No planning when off")
            if mode == "skip":
                return '[]'
            return '["alpha","beta"]'
        search.planner = planner
        if mode == "cancel":
            async def slow(query, question, started):
                await started()
                entered.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    stopped.append(query)
                    raise
            search.backend.search = slow
        class Agent:
            async def astream(self, **kwargs):
                captured.append((kwargs["system_prompt"], kwargs["input_prompt"]))
                message = Message("assistant", [TextPart("回答 [1]。另一结论 [2]。")])
                yield AgentMessageCompletedEvent(step=1, message=message)
                yield AgentCompletedEvent(step=1, response=AgentResponse(message=message,
                    usage=Usage(10, 2), last_usage=Usage(10, 2), finish_reason="stop", steps=1,
                    messages=[Message("user", [TextPart(kwargs["input_prompt"])]), message], task_message_start=0))
        runtime.runtime_host.get_plugin("agent").agent_factory.get_agent = lambda role: Agent()
        system = runtime.runtime_host.get_plugin("prompt")._system_prompt
        try:
            accepted = await runtime.submit("今天的资料", request_options={"web_search": mode != "off", "deep_search": False,
                "local_time": "2026-10-23T20:00:00+08:00", "time_zone": "Asia/Shanghai"})
            if mode == "cancel":
                await asyncio.wait_for(entered.wait(), 2)
                await runtime.cancel_task(accepted.task_id)
            async def finished():
                while not any(u.type == "task.finished" and u.task_id == accepted.task_id for u in updates) or runtime.snapshot().has_work:
                    await asyncio.sleep(.01)
            await asyncio.wait_for(finished(), 3)
            # Let cancellation cleanup finish publishing the final search snapshot.
            await asyncio.wait_for(asyncio.gather(*tuple(search.work.values()), return_exceptions=True), 1)
            progress = [u for u in updates if u.type == "web_search.updated"]
            if mode in {"off", "skip"}:
                assert search.backend.calls == [] and progress == []
            elif mode == "cancel":
                assert stopped and captured == []
                assert progress[-1].payload["status"] == "stopped"
            elif mode == "search-failed":
                assert progress[-1].payload["status"] == "error"
                assert "正常回答" in captured[0][1]
            elif mode == "fetch-failed":
                assert progress[-1].payload["total"] == 24
                assert progress[-1].payload["pages"] == []
                assert "未浏览正文" in captured[0][1]
            else:
                assert progress[-1].payload["total"] == 24
                assert len(progress[-1].payload["pages"]) == 3
                assert "正文事实" in captured[0][1]
                assert "content" not in str(progress[-1].payload["sources"])
            if captured:
                assert captured[0][0] == system
                assert "当前时间" not in captured[0][0]
                assert "2026-10-23 20:00:00+08:00" in captured[0][1]
            assert not any(u.type == "task.error" and u.payload.get("fatal") for u in updates)
        finally:
            await runtime.stop("test", timeout=2)
    asyncio.run(run())
