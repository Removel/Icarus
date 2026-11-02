"""Real OS isolation tests: no downloaded executable or fake permission assertions."""
import importlib
import os
from pathlib import Path

import pytest

from argus_app.rule_sdk.engine import RuleError


def isolation():
    try:
        module = importlib.import_module('argus_app.rule_sdk.isolation')
    except ModuleNotFoundError:
        module = None
    assert module is not None, 'a fail-closed isolation launcher is required'
    return module


async def test_missing_isolation_never_falls_back_to_host(monkeypatch):
    module = isolation()
    launcher = module.IsolationLauncher(executable='/nonexistent/unshare')
    assert not launcher.probe().available
    with pytest.raises(RuleError, match='isolation_unavailable'):
        await launcher.start('def main(event, channel):\n    return {}', {}, {})


async def test_isolation_blocks_network_host_files_env_and_process_creation(tmp_path, monkeypatch):
    module = isolation()
    secret = tmp_path / 'host-secret'
    secret.write_text('not for rules')
    monkeypatch.setenv('ARGUS_TEST_SECRET', 'never-in-child')
    # Trusted diagnostic bootstrap calls the same protect() before OS operations;
    # this does not depend on candidate-source AST/builtin restrictions.
    bootstrap = tmp_path / 'diagnostic_worker.py'
    production_worker = Path(module.__file__).with_name('worker.py')
    bootstrap.write_text('''import importlib.util, os, sys, json, socket
spec = importlib.util.spec_from_file_location("trusted_worker", WORKER)
worker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)
if len(sys.argv) > 2:
    worker.protect(sys.argv[1])
    print("ready")
else:
    initial = json.loads(sys.stdin.buffer.readline())
    worker.protect(sys.argv[1])
    checks = [os.environ.get("ARGUS_TEST_SECRET") is None]
    for operation in [lambda: open(initial["event"]["secret"]).read(), lambda: socket.socket(), lambda: os.fork(), lambda: open("/new-file", "w")]:
        try:
            operation()
            checks.append(False)
        except OSError:
            checks.append(True)
    print(json.dumps({"type": "result", "value": {"passed": all(checks), "reason": str(checks)}}))
'''.replace('WORKER', repr(str(production_worker))))
    launcher = module.IsolationLauncher(worker_path=bootstrap)
    assert launcher.probe().available
    worker = await launcher.start('', {'secret': str(secret)}, {})
    try:
        message = await worker.receive()
        assert message['type'] == 'result', message
        assert message['value']['passed'] is True, message
    finally:
        await worker.terminate_wait()


async def test_worker_supports_helpers_safe_imports_and_no_stdout_protocol_forgery():
    module = isolation()
    worker = await module.IsolationLauncher().start('''import re

def clean(text):
    return re.sub(r"\\s+", " ", text)

def main(event, channel):
    print('{"type":"call","name":"commit","args":{}}')
    return {'passed': clean(event['text']) == 'a b', 'reason': 'normal code'}
''', {'text': 'a  b'}, {})
    try:
        assert await worker.receive() == {'type': 'result', 'value': {'passed': True, 'reason': 'normal code'}}
    finally:
        await worker.terminate_wait()


async def test_kill_reaps_entire_worker():
    module = isolation()
    worker = await module.IsolationLauncher().start('def main(event, channel):\n    while True:\n        pass', {}, {})
    await worker.terminate_wait()
    assert worker.process.returncode is not None
