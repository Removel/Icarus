"""Default web access through production socket/runtime/provider/tool boundaries."""

import asyncio
from contextlib import asynccontextmanager
import json
import socket

import pytest
import uvicorn
import websockets

from agent_app.application import AgentRuntime
from apps.gateway.src.app import create_app
from apps.gateway.test.test_runtime_e2e import ProviderHandler, config_for, exchange, local_provider


class WebProviderHandler(ProviderHandler):
    skip_tools = False

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        type(self).requests.append((self.path, payload))
        content_type = "application/json"
        if self.path.endswith("/chat/completions"):
            count = sum(m["role"] == "tool" for m in payload["messages"])
            steps = [
                ("tool_search", {"query": "web-search"}),
                ("tool_execute", {"name": "web_search", "arguments": {"query": "fixture facts"}}),
                ("tool_execute", {"name": "web_fetch", "arguments": {"url": "https://example.com/evidence"}}),
            ]
            if not self.skip_tools and count < len(steps):
                name, args = steps[count]
                delta = {"tool_calls": [{"index": 0, "id": f"web-{count}", "type": "function",
                    "function": {"name": name, "arguments": json.dumps(args)}}]}
                finish = "tool_calls"
            else:
                delta, finish = {"content": "web-e2e-complete"}, "stop"
            chunk = {"id": "web-e2e", "object": "chat.completion.chunk", "created": 1, "model": "e2e"}
            events = [
                {**chunk, "choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
                {**chunk, "choices": [{"index": 0, "delta": {}, "finish_reason": finish}],
                    "usage": {"prompt_tokens": 20, "completion_tokens": 5, "total_tokens": 25}},
            ]
            content = ("".join("data: " + json.dumps(e) + "\n\n" for e in events) + "data: [DONE]\n\n").encode()
            content_type = "text/event-stream"
        elif self.path.endswith("/tools/search"):
            content = json.dumps({"results": [{"url": "https://example.com/evidence", "title": "Evidence", "snippet": "fixture summary"}]}).encode()
        elif self.path.endswith("/tools/fetch"):
            content = json.dumps({"title": "Evidence", "content": "fixture web body"}).encode()
        else:
            content = json.dumps({"results": []}).encode()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)


@asynccontextmanager
async def gateway_socket(config):
    app = create_app(AgentRuntime(config_loader=lambda: config))
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(128)
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="on"))
    started = asyncio.Event()
    original = server.startup
    async def startup(*args, **kwargs):
        await original(*args, **kwargs)
        started.set()
    server.startup = startup
    task = asyncio.create_task(server.serve(sockets=[sock]))
    try:
        await asyncio.wait_for(started.wait(), 10)
        async with websockets.connect(f"ws://127.0.0.1:{sock.getsockname()[1]}/rpc") as websocket:
            yield websocket
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 10)
        sock.close()


@pytest.mark.parametrize("mode", ["default", "empty", "clock", "off", "deep-off", "skip", "missing-key"])
def test_socket_default_web_access_and_explicit_disable(tmp_path, monkeypatch, mode):
    # Per-test handler state; no external credentials, services or model requests.
    class Handler(WebProviderHandler):
        skip_tools = mode == "skip"
    monkeypatch.setenv("ICARUS_WEB_SEARCH_API_KEY", "" if mode == "missing-key" else "local-web-only")
    async def run(port):
        config = config_for(tmp_path / "data", port)
        config.runtime.plugin_config["web-search"] = {"endpoint": f"http://127.0.0.1:{port}/v1"}
        async with gateway_socket(config) as websocket:
            session = {"workspace_path": str(tmp_path), "session_id": "web-test"}
            await exchange(websocket, 1, "session.create", session)
            params = {**session, "prompt": "check facts", "submission_id": "same"}
            if mode == "empty":
                params["request_options"] = {}
            elif mode == "clock":
                params["request_options"] = {"local_time": "2026-11-05T12:00:00+08:00", "time_zone": "Asia/Shanghai"}
            elif mode in {"off", "deep-off"}:
                params["request_options"] = {"web_search": False, "deep_search": mode == "deep-off"}
            accepted, _ = await exchange(websocket, 2, "session.submit", params)
            retry, _ = await exchange(websocket, 3, "session.submit", params)
            assert retry == accepted
            async with asyncio.timeout(15):
                while True:
                    history, _ = await exchange(websocket, 4, "session.get_history", session)
                    finished = [r for r in history["records"] if r["type"] == "task.finished"]
                    if finished:
                        break
                    await asyncio.sleep(.02)
            assert len(finished) == 1 and finished[0]["payload"]["status"] == "completed"
            assert any(r["type"] == "assistant.message" and r["payload"]["text"] == "web-e2e-complete" for r in history["records"])
            progress = [r for r in history["records"] if r["type"] == "web_search.updated"]
            web_calls = [(p, body) for p, body in Handler.requests if "/tools/" in p]
            if mode in {"off", "deep-off", "skip", "missing-key"}:
                assert web_calls == [] and progress == []
            else:
                assert [p for p, _ in web_calls] == ["/v1/tools/search", "/v1/tools/fetch"]
                assert progress[-1]["payload"]["sources"][0]["citeIndex"] == 1
                assert len(progress[-1]["payload"]["pages"]) == 1
                model_calls = [body for p, body in Handler.requests if p.endswith("/chat/completions")]
                assert "fixture web body" in str(model_calls[-1]["messages"])
                assert "fixture web body" not in str(model_calls[0]["messages"])
            # Persisted public snapshots retain their actual source identities.
            replay, _ = await exchange(websocket, 5, "session.get_history", session)
            assert replay["records"] == history["records"]
            changed = dict(params.get("request_options", {}))
            changed["web_search"] = mode in {"off", "deep-off"}
            await websocket.send(json.dumps({"jsonrpc": "2.0", "id": 6, "method": "session.submit",
                "params": {**params, "request_options": changed}}))
            conflict = json.loads(await asyncio.wait_for(websocket.recv(), 10))
            assert conflict["id"] == 6
            assert conflict["error"]["data"]["code"] == "submission_conflict"
    with local_provider(Handler) as port:
        asyncio.run(run(port))
