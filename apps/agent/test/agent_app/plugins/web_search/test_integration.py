"""Production plugin graph and ReAct loop; only LLM/HTTP boundaries are fixtures."""

import asyncio
import json

import httpx
import pytest

from agent_app.application.session_runtime import SessionRuntime
from agent_app.application.agent_runtime import AgentRuntime
from agent_app.plugins.persistence import SessionIdentity
from agent_app.plugins.memory.models import MemoryRecallResult
from agent_app.plugins.web_search.http_backend import SearchHttpBackend
from icarus.core.models.messages import LLMStreamChunk, ToolCall, Usage
from icarus.core.tools.tool_surface import RESIDENT_TOOLS
from apps.agent.test.agent_app.application.test_session_runtime import make_config
from apps.agent.test.icarus.core.agents.test_react_agent_stream import StreamQueueLLM


def tool_step(name, arguments, index):
    return [LLMStreamChunk(tool_calls=[ToolCall(f"call-{index}", name, arguments)],
        usage=Usage(10, 2), finish_reason="tool_call")]


def load_web_tools(index):
    return tool_step("tool_search", {"query": "web-search"}, index)


def web_tool_step(name, arguments, index):
    return tool_step("tool_execute", {"name": name, "arguments": arguments}, index)


def assert_frozen_tool_surface(calls):
    surface = calls[0][1]
    assert {definition.name for definition in surface} == set(RESIDENT_TOOLS)
    assert all(definitions == surface for _, definitions in calls)


@pytest.mark.parametrize("mode", ["off", "skip", "missing-key", "timeout", "on", "direct", "search-failed", "empty", "fetch-failed", "cancel-search", "cancel-fetch"])
def test_real_agent_drives_tools_and_progress(tmp_path, monkeypatch, mode):
    async def empty_recall(self, query, **kwargs):
        return MemoryRecallResult((), query)
    monkeypatch.setattr("agent_app.plugins.memory.mem0_http_adapter.Mem0HttpAdapter.arecall", empty_recall)

    async def run():
        updates, requests = [], []
        entered, stopped = asyncio.Event(), asyncio.Event()
        async def publish(update):
            updates.append(update)
        runtime = SessionRuntime(SessionIdentity.create(tmp_path, "search-test"),
            config=make_config(tmp_path / "data"), publish_update=publish)
        await runtime.start()
        plugin = runtime.runtime_host.get_plugin("web-search")
        await plugin.backend.aclose()

        async def transport(request):
            requests.append(request)
            if mode.startswith("cancel") or mode == "timeout":
                entered.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    stopped.set()
            if request.url.path.endswith("search"):
                if mode == "search-failed":
                    return httpx.Response(503)
                rows = [] if mode == "empty" else [
                    {"url": "https://example.com/a", "title": "A", "snippet": "summary fact"},
                    {"url": "https://example.com/b", "title": "B", "snippet": "other fact"}]
                return httpx.Response(200, json={"results": rows})
            return httpx.Response(200, json={"title": "Real page", "content": "" if mode == "fetch-failed" else "body facts"})

        plugin.backend = SearchHttpBackend("https://search.example", "test-only", transport=httpx.MockTransport(transport))
        if mode == "missing-key":
            plugin.backend.api_key = ""
        if mode == "timeout":
            plugin.deadline_seconds = .05
        final = [LLMStreamChunk(text_delta="回答 [1]。", usage=Usage(10, 2), finish_reason="stop")]
        if mode in {"direct", "fetch-failed", "cancel-fetch"}:
            steps = [web_tool_step("web_fetch", {"url": "https://example.com/a"}, 1)]
        elif mode == "skip":
            steps = []
        else:
            steps = [web_tool_step("web_search", {"query": "first query"}, 1)]
        if mode == "on":
            steps += [web_tool_step("web_fetch", {"url": "https://example.com/a"}, 2),
                web_tool_step("web_search", {"query": "followup"}, 3)]
        if steps:
            steps.insert(0, load_web_tools("load"))
        llm = StreamQueueLLM(steps + [final])
        # Keep production AgentAssembly, Observable wrappers, ReActAgent and ToolExecutor.
        runtime.runtime_host.get_plugin("agent").agent_factory.llm_factory.create_llm = lambda **kwargs: llm
        system = runtime.runtime_host.get_plugin("prompt")._system_prompt
        try:
            accepted = await runtime.submit("当前资料", request_options={"web_search": mode != "off",
                "deep_search": False, "local_time": "2026-10-26T20:00:00+08:00", "time_zone": "Asia/Shanghai"})
            if mode.startswith("cancel"):
                await asyncio.wait_for(entered.wait(), 5)
                await runtime.cancel_task(accepted.task_id)
            async def finished():
                while not any(u.type == "task.finished" and u.task_id == accepted.task_id for u in updates) or runtime.snapshot().has_work:
                    await asyncio.sleep(.01)
            await asyncio.wait_for(finished(), 8)
            progress = [u for u in updates if u.type == "web_search.updated"]
            assert {"web_search", "web_fetch"} <= set(runtime.tool_registry.names())
            assert_frozen_tool_surface(llm.calls)
            if mode != "skip":
                loaded = next(m for m in llm.calls[1][0] if m.role == "tool")
                loaded_result = json.loads(loaded.content[0].text)
                assert loaded_result["success"]
                assert "web_search" in loaded_result["output"]
                assert "web_fetch" in loaded_result["output"]
            assert llm.calls[0][0][0].content[0].text == system
            user = llm.calls[0][0][-1].content[0].text
            assert "2026-10-26 20:00:00+08:00" in user
            assert "当前时间" not in system
            assert all(set(u.payload) == {"status", "total", "queries", "sources", "pages", "browsing", "notice"}
                for u in progress)
            if mode in {"off", "skip", "missing-key"}:
                assert requests == [] and progress == []
            elif mode.startswith("cancel"):
                assert stopped.is_set()
                assert progress[-1].payload["status"] == "stopped"
                assert progress[-1].payload["browsing"] is False
                search_index = updates.index(progress[-1])
                finish_index = next(i for i, u in enumerate(updates) if u.type == "task.finished")
                assert search_index < finish_index
                assert progress[0].payload["browsing"] if mode == "cancel-fetch" else progress[0].payload["status"] == "searching"
            elif mode in {"search-failed", "fetch-failed", "timeout"}:
                assert progress[-1].payload["status"] == "error"
                assert progress[-1].payload["pages"] == []
            elif mode == "empty":
                assert progress[-1].payload["status"] == "empty"
            else:
                last = progress[-1].payload
                assert len(last["pages"]) == 1 and last["browsing"] is False
                assert last["total"] == (4 if mode == "on" else 0)
                assert last["sources"][0]["citeIndex"] == 1
                assert "content" not in str(last["sources"])
            assert plugin.tasks == {}
            assert not any(u.type == "task.error" and u.payload.get("fatal") for u in updates)
            # Tool messages really return to the next model step, rather than prefilled User Prompt.
            if mode == "on":
                assert len(llm.calls) == 5
                assert any(m.role == "tool" for m in llm.calls[2][0])
                assert "body facts" in str(llm.calls[3][0])
                assert "body facts" not in user
                # Reuse the actual session, with a new task and different options.
                for enabled in (False, True):
                    call_offset = len(llm.calls)
                    llm.streams.extend([
                        web_tool_step("web_fetch", {"url": "https://example.com/new"}, "before-load"),
                        load_web_tools("reload"),
                        web_tool_step("web_fetch", {"url": "https://example.com/new"}, 4),
                        final,
                    ])
                    before = len(requests)
                    accepted = await runtime.submit("next", request_options={"web_search": enabled})
                    await asyncio.wait_for(finished(), 8)
                    current = [u.payload for u in updates if u.type == "web_search.updated" and u.task_id == accepted.task_id]
                    assert len(requests) == before + int(enabled)
                    calls = llm.calls[call_offset:]
                    assert_frozen_tool_surface(calls)
                    assert calls[0][0][0].content[0].text == system
                    rejected = json.loads(next(m for m in reversed(calls[1][0]) if m.role == "tool").content[0].text)
                    assert rejected["success"] is False
                    assert "not loaded in this Run" in rejected["error"]
                    result = json.loads(next(m for m in reversed(calls[-1][0]) if m.role == "tool").content[0].text)
                    assert result["success"] is enabled
                    if enabled:
                        assert current[-1]["queries"] == [] and current[-1]["total"] == 0
                        assert len(current[-1]["sources"]) == 1 and current[-1]["sources"][0]["citeIndex"] == 1
                        assert result["output"]["budget"]["fetches_remaining"] == 2
                    else:
                        assert current == []
                    assert plugin.tasks == {}
        finally:
            await runtime.stop("test", timeout=3)
    asyncio.run(run())


def test_session_rebuild_cannot_publish_late_response_from_stopped_plugin(tmp_path, monkeypatch):
    async def empty_recall(self, query, **kwargs):
        return MemoryRecallResult((), query)
    monkeypatch.setattr("agent_app.plugins.memory.mem0_http_adapter.Mem0HttpAdapter.arecall", empty_recall)

    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        updates = []
        async def publish(update):
            updates.append(update)
        identity = SessionIdentity.create(tmp_path, "rebuild")
        config = make_config(tmp_path / "data")
        old = SessionRuntime(identity, config=config, publish_update=publish)
        await old.start()
        plugin = old.runtime_host.get_plugin("web-search")
        await plugin.backend.aclose()
        async def transport(request):
            entered.set()
            while not release.is_set():
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    continue
            return httpx.Response(200, json={"results": [{"url": "https://example.com/old", "title": "Old"}]})
        plugin.backend = SearchHttpBackend("https://search.example", "fixture", transport=httpx.MockTransport(transport))
        plugin.STOP_TIMEOUT = .02
        llm = StreamQueueLLM([load_web_tools("load"), web_tool_step("web_search", {"query": "old"}, 1)])
        old.runtime_host.get_plugin("agent").agent_factory.llm_factory.create_llm = lambda **kwargs: llm
        task = await old.submit("old task", request_options={"web_search": True})
        await asyncio.wait_for(entered.wait(), 5)
        try:
            with pytest.raises(RuntimeError, match="cleanup timed out"):
                await old.stop("rebuild", timeout=1)
            replacement = SessionRuntime(identity, config=config, publish_update=publish)
            await replacement.start()
            try:
                new_plugin = replacement.runtime_host.get_plugin("web-search")
                assert new_plugin is not plugin and new_plugin.tasks == {}
                count = len([u for u in updates if u.type == "web_search.updated" and u.task_id == task.task_id])
                pending = tuple(plugin._requests)
                release.set()
                if pending:
                    await asyncio.wait_for(asyncio.gather(*pending, return_exceptions=True), 1)
                assert len([u for u in updates if u.type == "web_search.updated" and u.task_id == task.task_id]) == count
                assert plugin.tasks == {} and new_plugin.tasks == {}
            finally:
                await replacement.stop("test", timeout=2)
        finally:
            release.set()
    asyncio.run(run())


@pytest.mark.parametrize("mode", ["complete", "cancel-search", "cancel-fetch"])
def test_application_persists_citations_and_cancel_order_with_real_sequences(tmp_path, monkeypatch, mode):
    async def empty_recall(self, query, **kwargs):
        return MemoryRecallResult((), query)
    monkeypatch.setattr("agent_app.plugins.memory.mem0_http_adapter.Mem0HttpAdapter.arecall", empty_recall)

    async def run():
        entered = asyncio.Event()
        async def transport(request):
            if mode.startswith("cancel"):
                entered.set()
                await asyncio.Event().wait()
            return httpx.Response(200, json={"results": [{"url": "https://example.com/a", "title": "A"}]}
                if request.url.path.endswith("search") else {"title": "A", "content": "body facts"})
        steps = [web_tool_step("web_fetch", {"url": "https://example.com/a"}, 1)] if mode == "cancel-fetch" else [
            web_tool_step("web_search", {"query": "first"}, 1)]
        if mode == "complete":
            steps += [web_tool_step("web_fetch", {"url": "https://example.com/a"}, 2),
                web_tool_step("web_search", {"query": "followup"}, 3)]
        steps.insert(0, load_web_tools("load"))
        llm = StreamQueueLLM(steps + [[LLMStreamChunk(text_delta="answer [1]", usage=Usage(10, 2), finish_reason="stop")]])
        class PreparedRuntime(SessionRuntime):
            async def start(self):
                await super().start()
                search = self.runtime_host.get_plugin("web-search")
                await search.backend.aclose()
                search.backend = SearchHttpBackend("https://search.example", "test-only", transport=httpx.MockTransport(transport))
                self.runtime_host.get_plugin("agent").agent_factory.llm_factory.create_llm = lambda **kwargs: llm
        api = AgentRuntime(config_loader=lambda: make_config(tmp_path / "data"), session_factory=PreparedRuntime)
        await api.start()
        subscription = api.subscribe_updates()
        try:
            await api.create_session(tmp_path, "history")
            accepted = await api.submit(tmp_path, "history", "facts", submission_id="test",
                request_options={"web_search": True})
            if mode.startswith("cancel"):
                await asyncio.wait_for(entered.wait(), 5)
                await api.cancel_task(tmp_path, "history", accepted.task_id)
            async def finished():
                while True:
                    update = await subscription.next_update()
                    if update.type == "task.finished" and update.task_id == accepted.task_id:
                        return update
            terminal = await asyncio.wait_for(finished(), 8)
            records, cursor = await api.get_session_history(tmp_path, "history")
            progress = [r for r in records if r.type == "web_search.updated"]
            assert progress and all(r.sequence is not None for r in records)
            assert progress[-1].sequence < terminal.sequence <= cursor
            if mode.startswith("cancel"):
                assert progress[-1].payload["status"] == "stopped"
                assert progress[-1].payload["browsing"] is False
            else:
                assert progress[-1].payload["queries"] == ["first", "followup"]
                assert len(progress[-1].payload["pages"]) == 1
                assert progress[-1].payload["sources"][0]["citeIndex"] == 1
            replay, replay_cursor = await api.get_session_history(tmp_path, "history")
            assert replay_cursor == cursor and replay == records
        finally:
            subscription.close()
            await api.stop()
    asyncio.run(run())
