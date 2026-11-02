"""Compile/policy checks must never execute a candidate module."""
import importlib

import pytest


def check(source):
    try:
        module = importlib.import_module('argus_app.rule_sdk.checks')
    except ModuleNotFoundError:
        module = None
    assert module is not None, 'file-level static checker is required'
    return module.check_source(source)


def test_file_helpers_constants_try_and_safe_import_compile():
    report = check('''import re
import json
THRESHOLD = 0.5

def clean(value):
    try:
        return re.sub(r"\\s+", " ", value) if isinstance(value, str) else ""
    except ValueError:
        return ""

def main(event, channel):
    text = clean(event.get("content", {}).get("text", ""))
    return {"passed": bool(text), "reason": json.dumps(text)}
''')
    assert report.passed, report.diagnostics
    assert len(report.source_hash) == 64
    assert report.policy_version


@pytest.mark.parametrize('source', [
    'raise RuntimeError("not executed")\ndef main(event, channel):\n    return {}',
    'print("no top level effects")\ndef main(event, channel):\n    return {}',
    'def helper(x=print("hidden")):\n    return x\ndef main(event, channel):\n    return {}',
    '@print("decorator")\ndef main(event, channel):\n    return {}',
])
def test_check_never_executes_top_level_or_hidden_calls(source, capsys):
    report = check(source)
    assert not report.passed
    assert capsys.readouterr().out == ''
    assert report.diagnostics[0].line >= 1
    assert report.diagnostics[0].stage in {'compile', 'policy'}


@pytest.mark.parametrize('body', [
    'import os', 'from socket import socket', 'import importlib', 'from re import *',
    'eval("1")', 'exec("1")', 'compile("1", "x", "exec")', 'open("secret")',
    'getattr(event, "text")', 'event.__class__', '__import__("os")',
    'globals()', 'capability = str', 'import json.decoder',
    'import re; re.enum.sys.modules["builtins"].exec("1")',
    'import re; re.enum.sys.modules["__main__"].emit({})',
])
def test_rejects_reflective_and_dynamic_imports(body):
    assert not check('def main(event, channel):\n    ' + body + '\n    return {}').passed


@pytest.mark.parametrize('declaration', [
    'def main(event, channel, extra):', 'def main(event, channel=None):',
    'async def main(event, channel):', 'def decide(event, channel):',
])
def test_main_signature_is_checked_without_loading(declaration):
    assert not check(declaration + '\n    return {}').passed


def test_syntax_errors_and_utf8_size_are_reported():
    report = check('def main(')
    assert not report.passed
    assert report.diagnostics[0].stage == 'compile'
    source = '# ' + '界' * 11000 + '\ndef main(event, channel):\n    return {}'
    assert len(source) < 32768
    assert not check(source).passed


def test_main_is_not_called_and_arithmetic_constants_are_supported():
    report = check('''LIMIT = 2 * 10

def main(event, channel):
    raise RuntimeError("must not run during lint")
''')
    assert report.passed


def test_ast_budget_and_finite_source_boundaries():
    source = 'def main(event, channel):\n    x = [' + ','.join('0' for _ in range(4100)) + ']\n    return {}'
    report = check(source)
    assert not report.passed
    assert any('limit' in x.code for x in report.diagnostics)
