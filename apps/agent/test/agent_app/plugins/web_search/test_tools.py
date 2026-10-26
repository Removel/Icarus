import asyncio
from copy import deepcopy

import pytest

from agent_app.plugins.web_search.factory import create_plugin
from icarus.core.contracts.context import ContextContributionEvent
from icarus.core.contracts.input import UserInputEvent
from icarus.core.hooks import HookRegistry
from icarus.core.run_control import TaskChannelRegistry
from icarus.core.contracts.input import InputFinishedEvent
from icarus.core.models.messages import ToolCall
from icarus.core.tools import ToolExecutor, ToolRegistry
from icarus.runtime.loop_runner import RuntimeLoopRunner
from agent_app.plugins.web_search.plugin import WebSearchPlugin
from agent_app.plugins.web_search.http_backend import SearchUnavailable
from apps.agent.test.agent_app.application.test_session_runtime import make_config


def test_factory_registers_search_and_fetch_without_prefetch(tmp_path):
    async def run():
        registration = create_plugin("web-search", tmp_path, "test", {
            "config_model": make_config(tmp_path / "data"),
            "hook_registry": HookRegistry(), "task_channels": TaskChannelRegistry(),
        }, {}, None)
        plugin = registration.plugin
        try:
            assert {tool.definition.name for tool in plugin.build_tools()} == {"web_search", "web_fetch"}
            events = []
            async def publish(event):
                events.append(event)
            plugin.bind_publisher(publish)
            await plugin.on_input("user-input", UserInputEvent(task_id="task", prompt="最新资料",
                request_options={"web_search": True}))
            assert len(events) == 1 and isinstance(events[0], ContextContributionEvent)
            assert "当前时间" in events[0].context_blocks[0].content
        finally:
            await plugin.stop()
    asyncio.run(run())


def test_input_snapshot_exists_before_terminal_context_contribution():
    async def run():
        plugin, backend, _, _ = await setup_plugin()
        options = {"web_search": True, "deep_search": True}
        async def publish(event):
            state = plugin.tasks[event.task_id]
            assert state.enabled and state.deep and state.prompt == "new question"
            assert isinstance(event, ContextContributionEvent) and event.status == "completed"
            assert backend.calls == []
        plugin.bind_publisher(publish)
        await plugin.on_input("user-input", UserInputEvent(task_id="next", prompt="new question", request_options=options))
        options["web_search"] = False
        assert plugin.tasks["next"].enabled
        await plugin.stop()
    asyncio.run(run())


class Backend:
    api_key = "test-only"
    def __init__(self):
        self.calls = []
        self.rows = [{"url": "https://example.com/a", "title": "A", "snippet": "fact"},
            {"url": "https://example.com/b", "title": "B", "snippet": "other fact"}]
        self.content = "page facts" * 1000
        self.fail = False
        self.closed = False

    async def search(self, query, question, started=None):
        if started:
            await started()
        self.calls.append(("search", query, question))
        if self.fail:
            raise SearchUnavailable("Search unavailable")
        return deepcopy(self.rows)

    async def fetch(self, url, started=None):
        if started:
            await started()
        self.calls.append(("fetch", url))
        if self.fail:
            raise SearchUnavailable("Fetch unavailable")
        return "Page", self.content

    async def aclose(self):
        self.closed = True


async def setup_plugin(*, enabled=True, deep=False, deadline=45, clock=None):
    backend = Backend()
    channels = TaskChannelRegistry()
    channel = channels.create("task")
    channel.mark_preparing_context()
    channel.start_run("run")
    options = {"clock": clock} if clock else {}
    plugin = WebSearchPlugin("web-search", backend=backend, task_channels=channels,
        deadline_seconds=deadline, **options)
    events = []
    async def publish(event):
        events.append(event)
    plugin.bind_publisher(publish)
    await plugin.on_input("user-input", UserInputEvent(task_id="task", prompt="original question",
        request_options={"web_search": enabled, "deep_search": deep}))
    registry = ToolRegistry()
    registry.register_many(plugin.build_tools())
    return plugin, backend, events, ToolExecutor(registry)


def test_search_then_fetch_then_search_keep_citations_and_old_snapshots():
    async def run():
        plugin, backend, events, executor = await setup_plugin()
        first = await executor.aexecute(ToolCall("s1", "web_search", {"query": "first"}), task_id="task")
        first_index = next(s["citeIndex"] for s in first.output["sources"] if s["url"].endswith("/a"))
        saved = deepcopy(first.output)
        saved_event = deepcopy(events[-1])
        assert first.success and len(first.output["sources"]) == 2
        assert not any(c[0] == "fetch" for c in backend.calls)
        page = await executor.aexecute(ToolCall("f1", "web_fetch", {"url": "https://www.example.com/a/?utm_source=x"}), task_id="task")
        assert page.success and page.output["citeIndex"] == first_index
        assert len(page.output["content"]) == 6000 and page.output["truncated"]
        backend.rows = [{"url": "https://example.com/c", "title": "C"},
            {"url": "https://example.com/a", "title": "A much richer title"}]
        second = await executor.aexecute(ToolCall("s2", "web_search", {"query": "followup"}), task_id="task")
        assert second.success
        assert first.output == saved and events[2] == saved_event
        assert {s["url"]: s["citeIndex"] for s in events[-1].sources}["https://example.com/a"] == first_index
        assert len(events[-1].sources) == 3 and len(events[-1].pages) == 1
        assert events[-1].total == 4 and events[-1].queries == ("first", "followup")
        assert all("content" not in s for event in events[1:] for s in event.sources)
        exhausted = await executor.aexecute(ToolCall("s3", "web_search", {"query": "again"}), task_id="task")
        assert not exhausted.success and len(backend.calls) == 3
        await plugin.stop()
    asyncio.run(run())


@pytest.mark.parametrize("arguments", [
    {"query": "x", "task_id": "other"}, {"query": "x", "budget": 99},
    {"query": "x", "_execution": {"task_id": "other"}},
    {"query": "x", "_execution": {"timeout_seconds": 10000}},
    {"query": "x" * 129}, {"query": " "},
])
def test_untrusted_arguments_never_change_identity_or_make_http(arguments):
    async def run():
        plugin, backend, events, executor = await setup_plugin()
        result = await executor.aexecute(ToolCall("bad", "web_search", arguments), task_id="task")
        assert not result.success and backend.calls == [] and len(events) == 1
        await plugin.stop()
    asyncio.run(run())


def test_legal_execution_controls_are_removed_and_sync_uses_host_loop():
    async def run():
        plugin, backend, events, executor = await setup_plugin()
        plugin._loop_runner = RuntimeLoopRunner()
        plugin._loop_runner.bind()
        args = {"query": "facts", "_execution": {"timeout_seconds": 1, "max_output_tokens": 512}}
        result = await asyncio.to_thread(executor.execute, ToolCall("sync", "web_search", args), task_id="task")
        assert result.success and backend.calls[0] == ("search", "facts", "original question")
        assert result.metadata["effective_timeout_seconds"] == 1
        direct = await plugin.build_tools()[0].ainvoke(args, task_id="task")
        assert not direct.success and len(backend.calls) == 1
        await plugin.stop()
    asyncio.run(run())


@pytest.mark.parametrize("mode", ["off", "missing", "unknown", "finished", "cancelled"])
def test_permissions_and_terminal_tasks_make_no_requests(mode):
    async def run():
        plugin, backend, events, executor = await setup_plugin(enabled=mode != "off")
        if mode == "missing":
            backend.api_key = ""
        if mode == "finished":
            await plugin.on_stop("user-input", InputFinishedEvent(task_id="task", status="completed"))
        if mode == "cancelled":
            plugin.task_channels.request_cancel("task", "stop")
        for name, args in [("web_search", {"query": "facts"}), ("web_fetch", {"url": "https://example.com/a"})]:
            result = await executor.aexecute(ToolCall(name, name, args), task_id="other" if mode == "unknown" else "task")
            assert not result.success
        assert backend.calls == [] and len(events) == 1
        await plugin.stop()
    asyncio.run(run())


@pytest.mark.parametrize("mode", ["empty-search", "empty-page", "search-error", "fetch-error", "direct"])
def test_empty_failure_and_direct_fetch_are_truthful(mode):
    async def run():
        plugin, backend, events, executor = await setup_plugin()
        backend.rows = []
        backend.content = "" if mode == "empty-page" else "real content"
        backend.fail = mode.endswith("error")
        name = "web_search" if "search" in mode else "web_fetch"
        args = {"query": "facts"} if name == "web_search" else {"url": "https://example.com/a"}
        result = await executor.aexecute(ToolCall("call", name, args), task_id="task")
        assert result.success == (mode in {"empty-search", "direct"})
        assert events[-1].browsing is False
        assert len(events[-1].pages) == (1 if mode == "direct" else 0)
        assert events[-1].total == 0
        if mode == "direct":
            assert events[1].browsing is True and events[-1].sources[0]["citeIndex"] == 1
        await plugin.stop()
    asyncio.run(run())


@pytest.mark.parametrize("url", ["http://127.0.0.1", "http://169.254.169.254/latest", "http://10.0.0.1", "http://localhost", "http://service.internal",
    "http://2130706433", "http://0x7f000001", "http://0177.0.0.1", "http://[::1]", "http://[::ffff:8.8.8.8]",
    "https://example.com:8443/", "https://user:pass@example.com/", "file:///secret", "https://example.com\\@127.0.0.1/",
    "http://[fe80::1%25eth0]/", "http://224.0.0.1/"])
def test_unsafe_fetch_urls_never_reach_backend(url):
    async def run():
        plugin, backend, events, executor = await setup_plugin()
        result = await executor.aexecute(ToolCall("unsafe", "web_fetch", {"url": url}), task_id="task")
        assert not result.success and backend.calls == [] and len(events) == 1
        await plugin.stop()
    asyncio.run(run())
