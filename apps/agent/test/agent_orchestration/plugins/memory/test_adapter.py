import httpx
import pytest

from apps.agent.src.agent_orchestration.plugins.memory import (
    Mem0HttpAdapter,
    MemoryBackendError,
)


def make_adapter(handler, *, preserve_input_language=True):
    client = httpx.Client(
        transport=httpx.MockTransport(handler), base_url="http://mem0.test",
        headers={"X-API-Key": "test-key"},
    )
    async_client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://mem0.test",
        headers={"X-API-Key": "test-key"},
    )
    return Mem0HttpAdapter(
        "http://mem0.test", user_id="removel", agent_id="icarus",
        preserve_input_language=preserve_input_language,
        client=client, async_client=async_client,
    )


def memory_json(memory_id="m1", run_id="global", content="fact"):
    return {
        "id": memory_id, "memory": content, "user_id": "removel",
        "agent_id": "icarus", "run_id": run_id, "score": 0.9,
        "created_at": "2026-09-15T00:00:00Z",
        "updated_at": "2026-09-15T01:00:00Z",
    }


def test_recall使用一次global_workspace_or查询并归一化():
    requests = []

    def handler(request):
        requests.append(request)
        assert request.headers["X-API-Key"] == "test-key"
        return httpx.Response(200, json={"results": [memory_json()]})

    adapter = make_adapter(handler)
    result = adapter.recall(
        "query", workspace_key="wk", scope=None, include_stopped=False,
        top_k=3, threshold=0.65,
    )
    payload = __import__("json").loads(requests[0].content)
    assert payload == {
        "query": "query",
        "filters": {
            "user_id": "removel", "agent_id": "icarus",
            "OR": [{"run_id": "global"}, {"run_id": "workspace:wk"}],
        },
        "top_k": 3, "threshold": 0.65, "show_expired": False,
    }
    assert result.items[0].ref == "memory:m1"
    assert result.items[0].scope == "global"


def test_async_recall使用同一协议并可关闭():
    async def run():
        adapter = make_adapter(
            lambda request: httpx.Response(200, json={"results": [memory_json()]})
        )
        result = await adapter.arecall(
            "query", workspace_key="wk", scope=None,
            include_stopped=False, top_k=3, threshold=0.65,
        )
        await adapter.aclose()
        return result

    result = __import__("asyncio").run(run())
    assert result.items[0].ref == "memory:m1"


def test_explicit_recall_scope和include_stopped直接映射():
    payloads = []

    def handler(request):
        payloads.append(__import__("json").loads(request.content))
        return httpx.Response(200, json={"results": []})

    adapter = make_adapter(handler)
    adapter.recall(
        "q", workspace_key="wk", scope="workspace",
        include_stopped=True, top_k=5, threshold=0.2,
    )
    assert payloads[0]["filters"]["run_id"] == "workspace:wk"
    assert "OR" not in payloads[0]["filters"]
    assert payloads[0]["show_expired"] is True


def test_remember使用infer_true并保留作用域元数据():
    requests = []

    def handler(request):
        requests.append(request)
        if request.method == "POST":
            return httpx.Response(200, json={"results": [{"id": "m1", "memory": "fact"}]})
        raise AssertionError(request.method)

    adapter = make_adapter(handler)
    records = adapter.remember(
        "fact", workspace_key="wk", scope="workspace",
        metadata={"origin": "explicit"},
    )
    payload = __import__("json").loads(requests[0].content)
    assert payload["messages"] == [{"role": "user", "content": "fact"}]
    assert payload["run_id"] == "workspace:wk"
    assert payload["infer"] is True
    assert payload["preserve_input_language"] is True
    assert records[0].run_id == "workspace:wk"


def test_remember可关闭输入语言保持():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"results": []})

    adapter = make_adapter(handler, preserve_input_language=False)
    adapter.remember(
        "fact", workspace_key="wk", scope="global", metadata={},
    )

    payload = __import__("json").loads(requests[0].content)
    assert payload["infer"] is True
    assert payload["preserve_input_language"] is False


def test_correct_stop_restore_delete使用精确id():
    requests = []
    current = memory_json()

    def handler(request):
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json=current)
        return httpx.Response(200, json={"message": "ok"})

    adapter = make_adapter(handler)
    adapter.correct("memory:m1", "fixed")
    adapter.set_expiration("memory:m1", "1970-01-01")
    adapter.set_expiration("memory:m1", None)
    adapter.delete("memory:m1")
    assert [(r.method, r.url.path) for r in requests] == [
        ("PUT", "/memories/m1"), ("GET", "/memories/m1"),
        ("PUT", "/memories/m1"), ("GET", "/memories/m1"),
        ("PUT", "/memories/m1"), ("GET", "/memories/m1"),
        ("DELETE", "/memories/m1"),
    ]


def test_history和安全错误映射():
    def history_handler(request):
        return httpx.Response(200, json=[{"event": "UPDATE", "new_memory": "new", "created_at": "now"}])

    history = make_adapter(history_handler).history("memory:m1")
    assert history[0].as_dict()["operation"] == "UPDATE"

    def error_handler(request):
        return httpx.Response(401, json={"detail": "bad token", "secret": "hidden"})

    with pytest.raises(MemoryBackendError, match="HTTP 401: bad token"):
        make_adapter(error_handler).get("memory:m1")


@pytest.mark.parametrize("ref", ["", "m1", "memory:"])
def test_ref格式必须稳定(ref):
    with pytest.raises(ValueError, match="memory:<id>"):
        make_adapter(lambda request: httpx.Response(500)).get(ref)
