import asyncio
import json

import httpx
import pytest

from agent_app.plugins.web_search.http_backend import SearchHttpBackend, SearchUnavailable
from agent_app.plugins.web_search.pipeline import (
    SearchPipeline, build_context, merge_sources, normalize_url, parse_queries,
)
from agent_app.plugins.web_search.plugin import WebSearchPlugin, time_context
from icarus.core.contracts.context import ContextContributionEvent
from icarus.core.contracts.input import UserInputEvent


class Backend:
    api_key = "test-only"
    def __init__(self, *, searches_fail=(), fetches_fail=()):
        self.searches_fail = searches_fail
        self.fetches_fail = fetches_fail
        self.calls = []
        self.cancelled = []
    async def search(self, query, question, started):
        await started()
        self.calls.append(("search", query))
        if query in self.searches_fail:
            raise SearchUnavailable("联网搜索暂不可用")
        offset = 0 if query == "alpha" else 6
        return [{"url": f"https://site{i % 5}.example/{i}", "title": f"标题{i}", "snippet": f"事实摘要{i}", "provider": "fixture"} for i in range(offset, offset + 12)]
    async def fetch(self, url):
        self.calls.append(("fetch", url))
        if url in self.fetches_fail or "all" in self.fetches_fail:
            raise ValueError("Unavailable page")
        return "真实页面", "正文事实" * 2000
    async def aclose(self):
        pass


async def planner(question):
    return '["alpha", "beta", "gamma"]'


def execute(backend, plan=planner, deep=False):
    snapshots = []
    async def report(**snapshot):
        snapshots.append(snapshot)
    content = asyncio.run(SearchPipeline(backend, plan).run("最新资料", deep=deep, report=report))
    return content, snapshots


def test_real_aggregate_count_dedup_fetch_and_citation_identity():
    backend = Backend()
    content, snapshots = execute(backend)
    last = snapshots[-1]
    assert last["total"] == 24
    assert len(last["sources"]) == 18
    assert len(last["pages"]) == 3
    assert len(content) <= 24000
    assert "外部不可信数据" in content
    for source in last["sources"][:3]:
        assert f"[{source['citeIndex']}] {source['title']}\nURL: {source['url']}" in content
    assert all(page["status"] == "done" for page in last["pages"])
    assert last["browsing"] is False
    assert backend.calls[:2] == [("search", "alpha"), ("search", "beta")]


def test_deep_search_opens_up_to_six_real_pages():
    _, snapshots = execute(Backend(), deep=True)
    assert snapshots[-1]["total"] == 36
    assert len(snapshots[-1]["pages"]) == 6


def test_partial_search_failure_uses_only_successful_batches():
    content, snapshots = execute(Backend(searches_fail={"alpha"}))
    assert snapshots[-1]["total"] == 12
    assert len(snapshots[-1]["pages"]) == 3
    assert "正文事实" in content


def test_search_failure_and_empty_results_do_not_abort_answer_context():
    backend = Backend(searches_fail={"alpha", "beta"})
    content, snapshots = execute(backend)
    assert snapshots[-1]["status"] == "error"
    assert snapshots[-1]["total"] == 0
    assert "正常回答" in content
    assert not any(call[0] == "fetch" for call in backend.calls)
    async def empty(query, question, started):
        await started()
        return []
    backend.search = empty
    content, snapshots = execute(backend)
    assert snapshots[-1]["status"] == "empty"
    assert "未搜索到" in content


def test_fetch_failures_never_create_browsed_pages_and_summaries_are_labelled():
    content, snapshots = execute(Backend(fetches_fail={"all"}))
    assert snapshots[-1]["status"] == "complete"
    assert snapshots[-1]["total"] == 24
    assert snapshots[-1]["pages"] == ()
    assert not any(s.get("pages") for s in snapshots)
    assert "搜索摘要（未浏览正文）" in content
    assert "正文事实" not in content


def test_partial_fetch_only_reports_successful_page_and_progress():
    url = "https://site0.example/10"
    _, snapshots = execute(Backend(fetches_fail={url}))
    assert len(snapshots[-1]["pages"]) == 2
    assert all(p["url"] != url for p in snapshots[-1]["pages"])
    assert any(s.get("browsing") and len(s["pages"]) == 1 for s in snapshots)


def test_planner_failure_and_malformed_json_fall_back_without_interrupting():
    async def broken(question):
        raise ValueError("Invalid planner response")
    backend = Backend()
    _, snapshots = execute(backend, broken)
    assert len([c for c in backend.calls if c[0] == "search"]) == 2
    assert snapshots[-1]["status"] == "complete"
    for text in ('{}', 'invalid', '[1,null]', '["only"]'):
        assert len(parse_queries(text, "最新资料", 5)) == 2
    assert parse_queries('["one","one","two",null]', "q", 5) == ["one", "two"]


def test_url_normalization_and_richer_duplicate_preserve_citation_sources():
    assert normalize_url("https://WWW.Example.com/page/?utm_source=x&ref=y&x=1#top") == "https://example.com/page?x=1"
    assert normalize_url("https://[2001:db8::1]:443/path#top") == "https://[2001:db8::1]/path"
    a = {"url": "https://www.example.com/page/?utm_source=x", "title": "old", "snippet": "summary"}
    b = {"url": "https://example.com/page", "title": "new", "provider": "real"}
    merged = merge_sources([[a], [b]])
    assert len(merged) == 1
    assert merged[0]["title"] == "new"
    assert "content" not in merged[0]


def test_cancellation_stops_all_in_flight_requests():
    async def run():
        backend = Backend()
        entered = asyncio.Event()
        cancelled = []
        async def slow(query, question, started):
            await started()
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.append(query)
                raise
        backend.search = slow
        async def report(**values):
            pass
        task = asyncio.create_task(SearchPipeline(backend, planner).run("question", deep=False, report=report))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 1)
        assert set(cancelled) == {"alpha", "beta"}
    asyncio.run(run())


def test_http_contract_paths_rate_gate_and_private_errors():
    async def run():
        starts, requests = [], []
        def transport(request):
            starts.append(asyncio.get_running_loop().time())
            requests.append(request)
            return httpx.Response(200, json={"results": []} if request.url.path.endswith("search") else {"title": "Title", "content": "Markdown"})
        backend = SearchHttpBackend("https://search.example", "private-key", transport=httpx.MockTransport(transport))
        try:
            await asyncio.gather(backend.search("alpha", "question"), backend.fetch("https://page.example/article"), backend.search("beta", "question"))
            assert [r.url.path for r in requests] == ["/v1/tools/search", "/v1/tools/fetch", "/v1/tools/search"]
            assert all(b - a >= .19 for a, b in zip(starts, starts[1:]))
            assert json.loads(requests[0].content)["count"] == 10
        finally:
            await backend.aclose()
    asyncio.run(run())


def test_off_means_zero_requests_and_local_clock_is_dynamic_user_context():
    async def run():
        backend = Backend()
        async def forbidden(*args):
            raise AssertionError("Planner must not be called with search disabled")
        plugin = WebSearchPlugin("web-search", backend=backend, planner=forbidden, task_channels=None)
        events = []
        async def publish(event):
            events.append(event)
        plugin.bind_publisher(publish)
        await plugin._prepare(UserInputEvent(task_id="off", prompt="今天几号", request_options={"web_search": False,
            "local_time": "2026-10-23T23:59:00+08:00", "time_zone": "Asia/Shanghai"}))
        assert backend.calls == []
        assert len(events) == 1 and isinstance(events[0], ContextContributionEvent)
        assert "2026-10-23 23:59:00+08:00" in events[0].context_blocks[0].content
        assert "Asia/Shanghai" in events[0].context_blocks[0].content
    asyncio.run(run())


def test_request_options_are_snapshotted_and_bounded():
    options = {"web_search": True}
    event = UserInputEvent(prompt="q", request_options=options)
    options["web_search"] = False
    assert event.request_options["web_search"] is True
    with pytest.raises(ValueError):
        UserInputEvent(prompt="q", request_options={"unbounded": "x" * 4096})


@pytest.mark.parametrize("mode", ["missing-key", "timeout"])
def test_unavailable_search_releases_context_without_exposing_private_errors(mode):
    async def run():
        backend = Backend()
        if mode == "missing-key":
            backend.api_key = ""
        cancelled = []
        async def slow_plan(question, clock):
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.append(True)
                raise
        plugin = WebSearchPlugin("web-search", backend=backend, planner=slow_plan,
            task_channels=None, deadline_seconds=.01)
        events = []
        async def publish(event):
            events.append(event)
        plugin.bind_publisher(publish)
        await asyncio.wait_for(plugin._prepare(UserInputEvent(task_id=mode, prompt="资料",
            request_options={"web_search": True})), 1)
        assert backend.calls == []
        assert cancelled == ([] if mode == "missing-key" else [True])
        assert events[0].status == "error"
        assert events[0].browsing is False
        assert isinstance(events[-1], ContextContributionEvent)
        assert "正常回答" in events[-1].context_blocks[0].content
        assert "test-only" not in str(events)
    asyncio.run(run())
