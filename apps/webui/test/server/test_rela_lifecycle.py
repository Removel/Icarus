"""Exercise the relationship launch configuration without requiring a POSIX shell."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest


def test_launcher_loads_server_only_rela_values_and_honors_process(monkeypatch, tmp_path):
    script = Path(__file__).resolve().parents[2] / 'scripts/icarus_start.py'
    spec = importlib.util.spec_from_file_location('rela_webui_launcher', script)
    launcher = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(launcher)
    env_file = tmp_path / '.env'
    env_file.write_text('ICARUS_RELATIONSHIP_ENDPOINT=http://127.0.0.1:9991\nRELA_MEM_API_TOKEN=isolated-config-token\nUNRELATED_FILE_VALUE=excluded\n', encoding='utf-8')
    monkeypatch.setattr(launcher, 'ENV_FILE', env_file)
    monkeypatch.delenv('ICARUS_RELATIONSHIP_ENDPOINT', raising=False)
    monkeypatch.delenv('RELA_MEM_API_TOKEN', raising=False)
    monkeypatch.delenv('UNRELATED_FILE_VALUE', raising=False)
    environment = launcher.build_environment()
    assert environment['ICARUS_RELATIONSHIP_ENDPOINT'] == 'http://127.0.0.1:9991'
    assert environment['RELA_MEM_API_TOKEN'] == 'isolated-config-token'
    assert 'UNRELATED_FILE_VALUE' not in environment
    monkeypatch.setenv('RELA_MEM_API_TOKEN', 'isolated-process-token')
    assert launcher.build_environment()['RELA_MEM_API_TOKEN'] == 'isolated-process-token'


@pytest.mark.parametrize('status', [200, 401])
@pytest.mark.parametrize('gateway', ['http-rejected', 'reentrant-error'])
def test_diagnostic_checks_rela_auth_without_printing_token_or_records(status, gateway, tmp_path):
    calls = []
    class Backend(BaseHTTPRequestHandler):
        def do_GET(self):
            calls.append((self.path, self.headers.get('Authorization')))
            if self.path.startswith('/v1/persons'):
                code = status
                body = {'items': [{'person_id': 'private-diagnostic-record'}]}
            elif self.path.startswith('/memories'):
                code, body = 200, {'results': []}
            elif self.path.startswith('/api/v1/kbs'):
                code, body = 200, {'knowledge_bases': []}
            else:
                code, body = 503, {}  # No Gateway is needed to verify the independent rela diagnostic.
            raw = json.dumps(body).encode()
            self.send_response(code)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
        def log_message(self, *_):
            pass
    backend = ThreadingHTTPServer(('127.0.0.1', 0), Backend)
    threading.Thread(target=backend.serve_forever, daemon=True).start()
    endpoint = f'http://127.0.0.1:{backend.server_port}'
    env = {**os.environ, **{key: endpoint for key in ('ICARUS_RELATIONSHIP_ENDPOINT', 'ICARUS_MEM0_ENDPOINT', 'ICARUS_OPENKB_ENDPOINT', 'ICARUS_GATEWAY_ENDPOINT')}, 'RELA_MEM_API_TOKEN': 'private-diagnostic-token'}
    command = [shutil.which('node')]
    if gateway == 'reentrant-error':
        # Node 22 can emit another error synchronously when a failed handshake
        # is closed. Reproduce that behavior on newer Node versions as well.
        preload = tmp_path / 'gateway-error.mjs'
        preload.write_text('''
globalThis.WebSocket = class extends EventTarget {
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSED = 3;
  readyState = WebSocket.CONNECTING;
  closeCalls = 0;
  constructor() {
    super();
    queueMicrotask(() => this.dispatchEvent(new Event('error')));
  }
  close() {
    if (++this.closeCalls > 1) throw new Error('Gateway socket closed twice');
    this.dispatchEvent(new Event('error'));
    this.readyState = WebSocket.CLOSED;
    this.dispatchEvent(new Event('close'));
  }
};
''', encoding='utf-8')
        command.extend(['--import', preload.as_uri()])
    command.append('server/check-services.mjs')
    try:
        result = subprocess.run(command, cwd=Path(__file__).resolve().parents[2], env=env, capture_output=True, text=True, timeout=15)
        output = result.stdout + result.stderr
        assert ('rela: ready' if status == 200 else 'rela: unavailable (rela: HTTP 401)') in output, output
        assert 'Mem0: ready' in output
        assert 'OpenKB: ready' in output
        assert 'Gateway: unavailable (Gateway: connection failed)' in output
        assert 'Exception' not in output
        assert 'Error:' not in output
        assert ('/v1/persons?limit=1', 'Bearer private-diagnostic-token') in calls
        assert 'private-diagnostic-token' not in output
        assert 'private-diagnostic-record' not in output
        assert result.returncode == 1  # Gateway deliberately unavailable; diagnostics remain non-blocking.
    finally:
        backend.shutdown()
        backend.server_close()
