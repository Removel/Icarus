"""Exercise retry policy at the wire boundary without real models or credentials."""
import asyncio
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest

from agent_app.model_provider.impl.anthropic_llm import AnthropicLLM
from agent_app.model_provider.impl.openai_llm import OpenAILLM
from icarus.core.models.messages import Message, TextPart


@pytest.fixture
def failing_endpoint():
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get('Content-Length', '0')))
            requests.append(self.path)
            body = b'{"error":{"type":"api_error","message":"synthetic failure"}}'
            self.send_response(500)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}', requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.parametrize('adapter', [OpenAILLM, AnthropicLLM])
@pytest.mark.parametrize('async_mode', [False, True])
def test_zero_retries_sends_only_one_wire_request(failing_endpoint, adapter, async_mode):
    async def run():
        endpoint, requests = failing_endpoint
        llm = adapter(model_name='synthetic-model', api_key='synthetic-key',
                      base_url=endpoint, max_tokens=2048, max_retries=0)
        try:
            with pytest.raises(Exception) as error:
                if async_mode:
                    await llm.ainvoke([Message('user', [TextPart('synthetic input')])])
                else:
                    await asyncio.to_thread(llm.invoke, [Message('user', [TextPart('synthetic input')])])
            assert getattr(error.value, 'status_code', None) == 500
            assert len(requests) == 1
        finally:
            await llm.aclose()
    asyncio.run(run())


@pytest.mark.parametrize('adapter', [OpenAILLM, AnthropicLLM])
@pytest.mark.parametrize('max_retries', [-1, True, '0', 1.5])
def test_adapter_rejects_invalid_retry_policy(adapter, max_retries):
    with pytest.raises(ValueError, match='max_retries'):
        adapter(model_name='synthetic', api_key='synthetic-key', max_tokens=2048,
                max_retries=max_retries)
