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
def test_diagnostic_checks_rela_auth_without_printing_token_or_records(status):
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
    try:
        result = subprocess.run([shutil.which('node'), 'server/check-services.mjs'], cwd=Path(__file__).resolve().parents[2], env=env, capture_output=True, text=True, timeout=15)
        output = result.stdout + result.stderr
        assert ('rela: ready' if status == 200 else 'rela: unavailable (rela: HTTP 401)') in output
        assert ('/v1/persons?limit=1', 'Bearer private-diagnostic-token') in calls
        assert 'private-diagnostic-token' not in output
        assert 'private-diagnostic-record' not in output
        assert result.returncode == 1  # Gateway deliberately unavailable; diagnostics remain non-blocking.
    finally:
        backend.shutdown()
        backend.server_close()
