import base64
import hashlib
import http.client
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest


ROOT = Path(__file__).resolve().parents[2]
AUTH = "Basic " + base64.b64encode(b"webui:test-password").decode()


@pytest.fixture(scope="module")
def production():
    # Build the actual deployed asset tree, also when this test runs on its own.
    subprocess.run([shutil.which("pnpm"), "build"], cwd=ROOT, check=True, capture_output=True)

    class Backend(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self):
            if self.headers.get("Upgrade") == "websocket":
                accept = base64.b64encode(
                    hashlib.sha1(
                        (
                            self.headers["Sec-WebSocket-Key"]
                            + "258EAFA5F-E914-47DA-95CA-C5AB0DC85B11"
                        ).encode()
                    ).digest()
                ).decode()
                self.send_response(101)
                self.send_header("Upgrade", "websocket")
                self.send_header("Connection", "Upgrade")
                self.send_header("Sec-WebSocket-Accept", accept)
                if self.headers.get("Sec-WebSocket-Extensions"):
                    self.send_header("Sec-WebSocket-Extensions", "permessage-deflate")
                self.end_headers()
                self.wfile.write(b"\x81\x05hello")
                self.wfile.flush()
                frame = self.rfile.read(2)
                mask = self.rfile.read(4)
                payload = self.rfile.read(frame[1] & 127)
                decoded = bytes(value ^ mask[index % 4] for index, value in enumerate(payload))
                self.wfile.write(bytes([0x81, len(decoded)]) + decoded)
                self.wfile.flush()
                self.close_connection = True
                return
            self.respond()

        def do_POST(self):
            self.respond()

        def respond(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", "0"))).decode()
            data = json.dumps(
                {"path": self.path, "headers": dict(self.headers), "body": body}
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *_):
            pass

    backend = ThreadingHTTPServer(("127.0.0.1", 0), Backend)
    threading.Thread(target=backend.serve_forever, daemon=True).start()
    with socket.socket() as candidate:
        candidate.bind(("127.0.0.1", 0))
        port = candidate.getsockname()[1]
    origin = f"http://127.0.0.1:{port}"
    endpoint = f"http://127.0.0.1:{backend.server_port}"
    env = {
        **os.environ,
        "ICARUS_WEBUI_HOST": "127.0.0.1",
        "ICARUS_WEBUI_PORT": str(port),
        "ICARUS_WEBUI_ORIGIN": origin,
        "ICARUS_WEBUI_USER": "webui",
        "ICARUS_WEBUI_PASSWORD": "test-password",
        "ICARUS_MEM0_ENDPOINT": endpoint,
        "ICARUS_OPENKB_ENDPOINT": endpoint,
        "ICARUS_GATEWAY_ENDPOINT": endpoint,
        "ICARUS_MEM0_API_KEY": "test-mem0-key",
        "ICARUS_OPENKB_API_TOKEN": "test-openkb-token",
    }
    process = subprocess.Popen(
        ["node", "server/index.mjs"],
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        for _ in range(100):
            if process.poll() is not None:
                raise AssertionError(process.stderr.read().decode())
            try:
                connection = http.client.HTTPConnection("127.0.0.1", port, timeout=1)
                connection.request("GET", "/health")
                if connection.getresponse().status == 200:
                    break
            except OSError:
                time.sleep(0.05)
            finally:
                connection.close()
        else:
            raise AssertionError("Production server did not start")
        yield port, origin, env
    finally:
        process.terminate()
        process.communicate(timeout=10)
        backend.shutdown()
        backend.server_close()


def request(production, method="GET", path="/", body=None, headers=None):
    connection = http.client.HTTPConnection("127.0.0.1", production[0], timeout=5)
    try:
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        connection.close()


def test_static_authentication_and_paths(production):
    assert request(production)[0] == 401
    status, headers, body = request(production, headers={"Authorization": AUTH})
    assert status == 200
    assert b"<html" in body
    assert "text/html" in headers["Content-Type"]
    for path in ["/.env", "/../package.json", "/%2e%2e/server/index.mjs", "/%5c..%5cpackage.json"]:
        assert request(production, path=path, headers={"Authorization": AUTH})[0] in (400, 404)
    assert request(production, path="/health")[0] == 200
    assert (
        request(
            production,
            path="/api/mem0/memories",
            headers={"Authorization": AUTH, "Host": "untrusted.example"},
        )[0]
        == 403
    )


def test_http_proxy_injects_only_backend_credentials(production):
    status, _, body = request(
        production,
        "POST",
        "/api/mem0/memories?q=test",
        '{"text":"hello"}',
        {
            "Authorization": AUTH,
            "Origin": production[1],
            "Content-Type": "application/json",
            "Cookie": "private=secret",
            "X-API-Key": "untrusted",
            "X-Forwarded-Host": "evil",
        },
    )
    assert status == 200
    value = json.loads(body)
    assert value["path"] == "/memories?q=test"
    assert json.loads(value["body"]) == {"text": "hello"}
    headers = {key.lower(): value for key, value in value["headers"].items()}
    assert headers["x-api-key"] == "test-mem0-key"
    assert not {"authorization", "cookie", "x-forwarded-host"} & headers.keys()
    status, _, body = request(production, path="/api/v1/kbs", headers={"Authorization": AUTH})
    assert status == 200
    assert json.loads(body)["headers"]["authorization"] == "Bearer test-openkb-token"
    for origin in ("https://evil.example", None):
        headers = {"Authorization": AUTH}
        if origin:
            headers["Origin"] = origin
        assert request(production, "POST", "/api/v1/init", "{}", headers)[0] == 403


def test_websocket_proxy_auth_origin_and_forwarding(production):
    def connect(authorization, origin, extensions=False):
        with socket.create_connection(("127.0.0.1", production[0]), timeout=5) as client:
            extra = "Sec-WebSocket-Extensions: permessage-deflate\r\n" if extensions else ""
            handshake = (
                f"GET /rpc HTTP/1.1\r\nHost: 127.0.0.1:{production[0]}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Version: 13\r\nSec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\nAuthorization: {authorization}\r\nOrigin: {origin}\r\n{extra}\r\n"
            ).encode()
            mask = b"abcd"
            frame = (
                b"\x81\x84"
                + mask
                + bytes(value ^ mask[index % 4] for index, value in enumerate(b"ping"))
            )
            client.sendall(handshake + frame)
            result = b""
            while chunk := client.recv(4096):
                result += chunk
            return result

    assert b"401 Unauthorized" in connect("", production[1])
    assert b"403 Forbidden" in connect(AUTH, "https://evil.example")
    result = connect(AUTH, production[1])
    assert b"101 Switching Protocols" in result
    assert b"\x81\x05hello" in result
    assert b"\x81\x04ping" in result
    assert b"sec-websocket-extensions: permessage-deflate" in connect(
        AUTH, production[1], extensions=True
    )


def test_public_binding_requires_authentication(production):
    env = {
        **production[2],
        "ICARUS_WEBUI_HOST": "0.0.0.0",
        "ICARUS_WEBUI_USER": "",
        "ICARUS_WEBUI_PASSWORD": "",
    }
    result = subprocess.run(
        ["node", "server/index.mjs"], cwd=ROOT, env=env, capture_output=True, timeout=10
    )
    assert result.returncode != 0
    assert b"Non-loopback listening requires" in result.stderr


def test_production_assets_exclude_demo_content(production):
    scripts = list((ROOT / 'apps' / 'shell' / 'dist' / 'assets').glob('*.js'))
    assert scripts
    for script in scripts:
        content = script.read_text(encoding='utf-8')
        assert '工作台阅读体验访谈' not in content
        assert '知识检索与来源追溯实验' not in content
        assert '浏览示例' not in content
