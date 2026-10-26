import asyncio
import json

import httpx
import pytest

from agent_app.plugins.web_search.http_backend import SearchHttpBackend, SearchUnavailable
from agent_app.plugins.web_search.pipeline import normalize_url, parse_sources


def test_source_parsing_rejects_invalid_urls_and_never_counts_search_content_as_fetch():
    rows = [{"url": "file:///secret", "title": "bad"},
            {"url": "https://example.com", "content": "not fetched", "summary": "fact"}]
    sources = parse_sources(rows)
    assert len(sources) == 1 and sources[0]["snippet"] == "fact"
    assert "content" not in sources[0]
    with pytest.raises(ValueError):
        parse_sources({"results": []})


def test_url_normalization():
    assert normalize_url("https://WWW.Example.com/page/?utm_source=x&ref=y&x=1#top") == "https://example.com/page?x=1"
    assert normalize_url("https://[2001:db8::1]:443/path#top") == "https://[2001:db8::1]/path"


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


def test_rate_gate_is_shared_by_backend_instances_and_wait_can_timeout():
    async def run():
        starts = []
        def transport(request):
            starts.append(asyncio.get_running_loop().time())
            return httpx.Response(200, json={"results": []})
        first = SearchHttpBackend("https://search.example", "fixture", transport=httpx.MockTransport(transport))
        second = SearchHttpBackend("https://other.example", "fixture", transport=httpx.MockTransport(transport))
        try:
            await first.search("one", "question")
            with pytest.raises(TimeoutError):
                async with asyncio.timeout(.02):
                    await second.search("not-started", "question")
            assert len(starts) == 1
            await second.search("two", "question")
            assert starts[1] - starts[0] >= .19
        finally:
            await first.aclose()
            await second.aclose()
    asyncio.run(run())


@pytest.mark.parametrize("status,message", [
    (401, "密钥无效"),
    (402, "余额不足"),
    (403, "拒绝访问"),
    (429, "请求过于频繁"),
    (404, "不支持"),
])
def test_search_http_errors_explain_configuration_without_exposing_response(status, message):
    async def run():
        def transport(request):
            return httpx.Response(status, text="private-key and private provider details")
        backend = SearchHttpBackend("https://search.example", "private-key", transport=httpx.MockTransport(transport))
        try:
            with pytest.raises(SearchUnavailable) as caught:
                await backend.search("query", "question")
            assert message in str(caught.value)
            assert "private-key" not in str(caught.value)
            assert "provider details" not in str(caught.value)
        finally:
            await backend.aclose()
    asyncio.run(run())


def test_fetch_security_rejection_is_reported_without_claiming_missing_search_permission():
    async def run():
        backend = SearchHttpBackend("https://search.example", "private-key",
            transport=httpx.MockTransport(lambda request: httpx.Response(403)))
        try:
            with pytest.raises(SearchUnavailable, match="安全策略"):
                await backend.fetch("https://page.example/article")
        finally:
            await backend.aclose()
    asyncio.run(run())
