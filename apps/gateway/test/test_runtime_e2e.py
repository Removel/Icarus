"""Socket-level offline E2E: real Gateway/runtime/plugins/store and provider adapter.

Only external HTTP services are supplied by local deterministic fixtures. The Agent,
LLM adapter, tool executor, bus, plugin graph and persistence are production objects.
"""

import asyncio
import json
import socket
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import uvicorn
import websockets

from agent_app.application import AgentRuntime
from agent_app.config import ConfigModel
from apps.gateway.src.app import create_app


class ProviderHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    requests = []

    def log_message(self, *_args):
        pass

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        type(self).requests.append((self.path, payload))
        if self.path.endswith("/chat/completions"):
            marker = "e2e-complete"
            content = (
                'data: ' + json.dumps({"id": "e2e", "object": "chat.completion.chunk", "created": 1,
                    "model": "e2e", "choices": [{"index": 0, "delta": {"content": marker}, "finish_reason": None}]}) + '\n\n' +
                'data: ' + json.dumps({"id": "e2e", "object": "chat.completion.chunk", "created": 1,
                    "model": "e2e", "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 4, "completion_tokens": 2, "total_tokens": 6}}) + '\n\n' +
                'data: [DONE]\n\n'
            ).encode()
            content_type = "text/event-stream"
        else:
            content = json.dumps({"results": []}).encode()
            content_type = "application/json"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)


@contextmanager
def local_provider(handler=ProviderHandler):
    handler.requests = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def config_for(data_dir, provider_port):
    model = {"model_name": "e2e", "context_window": 128000, "max_tokens": 100,
             "temperature": 0, "default_think_level": "low"}
    endpoint = f"http://127.0.0.1:{provider_port}"
    return ConfigModel(
        openai_base_url=endpoint + "/v1", anthropic_base_url=endpoint,
        openai_api_key="local-e2e-only", icarus_data_dir=data_dir,
        model_settings={"thinking": model, "perception": model},
        runtime={"plugin_config": {"memory": {"user_id": "e2e", "agent_id": "e2e", "endpoint": endpoint},
                                   "knowledge": {"knowledge_base": "e2e", "endpoint": endpoint}}},
    )


async def exchange(websocket, request_id, method, params):
    await websocket.send(json.dumps({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}))
    updates = []
    while True:
        item = json.loads(await asyncio.wait_for(websocket.recv(), 10))
        if item.get("id") == request_id:
            assert "error" not in item, item
            return item["result"], updates
        updates.append(item)


async def run_server(config, workspace, *, restored):
    runtime = AgentRuntime(config_loader=lambda: config)
    app = create_app(runtime)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(128)
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="on"))
    started = asyncio.Event()
    startup = server.startup
    async def start(*args, **kwargs):
        await startup(*args, **kwargs)
        started.set()
    server.startup = start
    task = asyncio.create_task(server.serve(sockets=[sock]))
    try:
        await asyncio.wait_for(started.wait(), 10)
        async with httpx.AsyncClient() as client:
            health = await client.get(f"http://127.0.0.1:{port}/health")
            assert health.json() == {"status": "ready"}
        async with websockets.connect(f"ws://127.0.0.1:{port}/rpc") as websocket:
            params = {"workspace_path": str(workspace), "session_id": "e2e"}
            if not restored:
                created, _ = await exchange(websocket, 1, "session.create", params)
                await exchange(websocket, 2, "session.subscribe", {"workspace_key": created["workspace_key"], "session_id": "e2e"})
            else:
                history, _ = await exchange(websocket, 3, "session.get_history", params)
                assert any(r["type"] == "assistant.message" and r["payload"]["text"] == "e2e-complete" for r in history["records"])
            await exchange(websocket, 4, "session.submit", params | {"prompt": "remember isolated e2e input", "submission_id": "second" if restored else "first"})
            for _ in range(100):
                history, _ = await exchange(websocket, 5, "session.get_history", params)
                finished = [r for r in history["records"] if r["type"] == "task.finished"]
                if len(finished) >= (2 if restored else 1):
                    break
                await asyncio.sleep(0.02)
            assert len(finished) == (2 if restored else 1)
            assert all(r["payload"]["status"] == "completed" for r in finished)
            assert history["history_cursor"] >= len(history["records"])
            result, _ = await exchange(websocket, 6, "session.unload", params)
            assert result["status"] == "unloaded"
            return history["history_cursor"]
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 10)
        sock.close()


def test_real_gateway_socket_runtime_and_restart(tmp_path):
    with local_provider() as port:
        config = config_for(tmp_path / "data", port)
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        first_cursor = asyncio.run(run_server(config, workspace, restored=False))
        second_cursor = asyncio.run(run_server(config, workspace, restored=True))
    assert second_cursor > first_cursor
    model_calls = [payload for path, payload in ProviderHandler.requests if path.endswith("/chat/completions")]
    assert len(model_calls) == 2
    assert any("e2e-complete" in str(message.get("content")) for message in model_calls[1]["messages"])
    assert (tmp_path / "data" / "icarus.db").is_file()
    assert list((tmp_path / "data").glob("workspaces/*/sessions/e2e/plugin-state/blackboard.json"))
