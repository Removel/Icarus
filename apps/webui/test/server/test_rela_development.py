"""Exercise the real Vite proxy against an isolated local upstream."""
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


def test_vite_rela_proxy_strips_prefix_and_private_headers():
    class Backend(BaseHTTPRequestHandler):
        def do_GET(self):
            data = json.dumps({'path': self.path, 'headers': dict(self.headers)}).encode()
            self.send_response(401 if self.path.startswith('/v1/private') else 200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(data)
        def log_message(self, *_):
            pass

    backend = ThreadingHTTPServer(('127.0.0.1', 0), Backend)
    threading.Thread(target=backend.serve_forever, daemon=True).start()
    with socket.socket() as candidate:
        candidate.bind(('127.0.0.1', 0))
        port = candidate.getsockname()[1]
    env = {**os.environ, 'ICARUS_RELATIONSHIP_ENDPOINT': f'http://127.0.0.1:{backend.server_port}', 'RELA_MEM_API_TOKEN': 'isolated-vite-token'}
    # Direct node invocation gives this test a single owned process to terminate on Windows.
    root = Path(__file__).resolve().parents[2]
    vite = root / 'apps/shell/node_modules/vite/bin/vite.js'
    process = subprocess.Popen([shutil.which('node'), str(vite), '--host', '127.0.0.1', '--port', str(port), '--strictPort'], cwd=root / 'apps/shell', env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    def request(path, headers=None):
        conn = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
        try:
            conn.request('GET', path, headers=headers or {})
            response = conn.getresponse()
            return response.status, response.read()
        finally:
            conn.close()
    try:
        for _ in range(150):
            if process.poll() is not None:
                raise AssertionError(process.stderr.read().decode(errors='replace'))
            try:
                if request('/api/rela/v1/persons?limit=1')[0] == 200:
                    break
            except OSError:
                time.sleep(.1)
        else:
            raise AssertionError('Isolated Vite proxy did not become ready')
        status, raw = request('/api/rela/v1/persons?query=%E7%8E%8B&cursor=a%2Bb', {'Authorization': 'Basic browser-private', 'Cookie': 'secret=value', 'X-API-Key': 'browser-private'})
        body = json.loads(raw)
        assert status == 200
        assert body['path'] == '/v1/persons?query=%E7%8E%8B&cursor=a%2Bb'
        headers = {key.lower(): value for key, value in body['headers'].items()}
        assert headers['authorization'] == 'Bearer isolated-vite-token'
        assert 'cookie' not in headers and 'x-api-key' not in headers
        assert request('/api/rela/v1/private')[0] == 401
        status, raw = request('/src/App.tsx')
        assert status == 200 and b'isolated-vite-token' not in raw
    finally:
        process.terminate()
        process.communicate(timeout=10)
        backend.shutdown()
        backend.server_close()
