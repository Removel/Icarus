import asyncio
from pathlib import Path

import pytest

from argus_app.rule_sdk.engine import RuleEngine, RuleError, lint
from argus_app.rule_sdk.files import RuleFiles


def test_files_are_immutable_hashed_and_root_confined(tmp_path):
    files = RuleFiles(tmp_path)
    source = 'def decide(event, channel):\n    return {"passed": True, "reason": "match"}\n'
    ref = files.publish(source, 'channel-1')
    assert files.read(ref['rule_path'], ref['rule_hash']) == source
    with pytest.raises(RuleError):
        files.read('../outside.py', ref['rule_hash'])
    with pytest.raises(RuleError):
        files.read(ref['rule_path'], 'wrong')


@pytest.mark.parametrize('source', [
    'import os',
    'def decide(event, channel):\n    return event.__class__',
    'def decide(event, channel):\n    return open("/etc/passwd").read()',
    'def decide(event, channel):\n    return eval("1")',
    'def decide(event, channel):\n    return getattr(event, "secret")',
    'def decide(event, channel):\n    return (lambda: 1)()',
])
def test_lint_blocks_unsafe_python(source):
    with pytest.raises(RuleError):
        lint(source)


@pytest.mark.asyncio
async def test_worker_calls_json_broker_without_host_exec():
    calls = []
    async def call(name, args):
        calls.append((name, args))
        return {'match': True}
    result = await RuleEngine().run('''def decide(event, channel):
    result = capability("semantic", {"text": event["content"]["text"]})
    return {"passed": result["match"], "reason": "semantic", "evidence_refs": [event["id"]]}
''', {'id': 'obs', 'content': {'text': 'hello'}}, {'id': 'ch'}, call)
    assert result == {'passed': True, 'reason': 'semantic', 'evidence_refs': ['obs']}
    assert calls == [('semantic', {'text': 'hello'})]


@pytest.mark.asyncio
async def test_worker_wall_limit_and_bounded_errors():
    async def call(name, args):
        return {}
    with pytest.raises(RuleError) as error:
        await RuleEngine(timeout=0.15).run('def decide(event, channel):\n    while True:\n        pass', {}, {}, call)
    assert error.value.code == 'rule_timeout'
    assert len(str(error.value)) <= 512


@pytest.mark.asyncio
async def test_broker_timeout_kills_child():
    async def call(name, args):
        await asyncio.sleep(20)
    with pytest.raises(RuleError) as error:
        await RuleEngine(timeout=0.15).run('def decide(event, channel):\n    return capability("semantic", {})', {}, {}, call)
    assert error.value.code == 'rule_timeout'


@pytest.mark.asyncio
@pytest.mark.parametrize('text', ['x' * 300000, '消息' * 160000], ids=['large-ascii', 'large-unicode'])
async def test_worker_large_valid_observation_is_not_truncated(text):
    async def call(name, args):
        return {}
    result = await RuleEngine().run('def decide(event, channel):\n    return {"passed": True, "reason": "large"}', {'content': {'text': text}}, {}, call)
    assert result['passed'] is True


@pytest.mark.asyncio
async def test_capability_call_budget_is_three_and_semantic_once():
    calls = []
    async def call(name, args):
        calls.append(name)
        return {}
    with pytest.raises(RuleError) as error:
        await RuleEngine().run('def decide(event, channel):\n    for i in range(4):\n        capability("history", {})\n    return {"passed": False, "reason": "x"}', {}, {}, call)
    assert error.value.code == 'rule_limit'
    assert len(calls) == 3
    calls.clear()
    with pytest.raises(RuleError) as error:
        await RuleEngine().run('def decide(event, channel):\n    capability("semantic", {})\n    return capability("semantic", {})', {}, {}, call)
    assert error.value.code == 'rule_limit'
    assert len(calls) == 1
