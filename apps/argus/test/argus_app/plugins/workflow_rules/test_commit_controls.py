"""The rule checker never runs source; commits prepare exact source before DB CAS."""
from hashlib import sha256

import pytest

from apps.argus.test.argus_app.infrastructure.test_commit import delivered, payload
from apps.argus.test.argus_app.infrastructure.test_store import store
from argus_app.plugins.workflow_rules.plugin import WorkflowRules
from argus_app.rule_sdk import RuleEngine, RuleFiles


async def request(store, round_, kind, body, rid):
    return await store.ingest(f"agent:{round_['binding_id']}", rid, kind, body,
                              {'binding_id': round_['binding_id'], 'payload': body})


async def test_rule_check_never_calls_engine_or_models(store, tmp_path):
    ch, items, round_ = await delivered(store)
    class NeverRun:
        async def evaluate(self, *args):
            raise AssertionError('lint executed source')
    rules = RuleFiles(tmp_path)
    workflow = WorkflowRules('workflow-rules', store, NeverRun(), rules)
    source = 'def main(event, channel):\n    raise RuntimeError("not run in check")'
    row = await request(store, round_, 'rule.check_requested', {'source': source}, 'check')
    result = await workflow._control(row)
    assert result['applied'] is True
    assert result['passed'] is True
    assert 'decisions' not in result
    assert list(tmp_path.rglob('*.py')) == []
    assert (await store.channel(ch.id)).version == 1


async def test_commit_prepares_exact_source_and_closes_round(store, tmp_path):
    ch, items, round_ = await delivered(store)
    files = RuleFiles(tmp_path)
    workflow = WorkflowRules('workflow-rules', store, RuleEngine(), files)
    source = 'def main(event, channel):\n    return {"passed": False, "reason": "new stage"}'
    row = await request(store, round_, 'agent.commit_requested', payload(ch, items, rule_source=source, progress='new'), 'commit')
    result = await workflow._control(row)
    assert result.get('committed') is True, result
    current = await store.channel(ch.id)
    assert current.rule_hash == sha256(source.encode()).hexdigest()
    assert files.read(current.rule_path, current.rule_hash) == source
    assert await workflow._control(row) == result


async def test_file_failure_leaves_old_state_and_rule(store, tmp_path, monkeypatch):
    ch, items, round_ = await delivered(store)
    files = RuleFiles(tmp_path)
    workflow = WorkflowRules('workflow-rules', store, RuleEngine(), files)
    before = (await store.channel(ch.id)).model_dump()
    def fail(*args):
        raise OSError('disk unavailable')
    monkeypatch.setattr(files, 'publish', fail)
    row = await request(store, round_, 'agent.commit_requested', payload(ch, items, rule_source='def main(event, channel):\n    return {}', progress='bad'), 'failed-file')
    with pytest.raises(OSError, match='disk unavailable'):
        await workflow._control(row)
    assert await store.commit_status(round_['binding_id'], row.message_id) == {'state': 'pending'}
    assert (await store.channel(ch.id)).model_dump() == before


async def test_caller_cannot_supply_prepared_path_hash(store, tmp_path):
    ch, items, round_ = await delivered(store)
    files = RuleFiles(tmp_path)
    workflow = WorkflowRules('workflow-rules', store, RuleEngine(), files)
    row = await request(store, round_, 'agent.commit_requested', payload(ch, items, rule_hash='a' * 64, rule_path=f'{ch.id}/' + 'a' * 64 + '.py'), 'forged')
    result = await workflow._control(row)
    assert result['applied'] is False


async def test_transient_transaction_failure_remains_pending_then_replays(store, tmp_path, monkeypatch):
    ch, items, round_ = await delivered(store)
    files = RuleFiles(tmp_path)
    workflow = WorkflowRules('workflow-rules', store, RuleEngine(), files)
    row = await request(store, round_, 'agent.commit_requested', payload(ch, items, progress='retry succeeds'), 'transient')
    original = store._save
    async def fail_after_save(conn, channel, publish=False):
        await original(conn, channel, publish)
        raise RuntimeError('transient database failure')
    monkeypatch.setattr(store, '_save', fail_after_save)
    with pytest.raises(RuntimeError, match='transient database'):
        await workflow._control(row)
    assert await store.commit_status(round_['binding_id'], row.message_id) == {'state': 'pending'}
    assert (await store.channel(ch.id)).progress == ''
    monkeypatch.setattr(store, '_save', original)
    result = await workflow._control(row)
    assert result.get('committed') is True
    assert (await store.channel(ch.id)).progress == 'retry succeeds'
