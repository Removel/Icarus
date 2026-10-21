from pathlib import Path
import pytest

from apps.argus.src.common.services.rule_service import (
    RuleValidationError, candidate_semantic_rules, evaluate_mechanical, load_rule_set,
)


def load(tmp_path, entries):
    path = tmp_path / 'rules.yaml'
    path.write_text('source: qq\nversion: 1\ndefault: {decision: ignore, reason: silence}\nrules:\n' + entries)
    return load_rule_set(path, known_facts={'mentions_bot', 'scope_kind', 'text', 'count'})


def test_priority_then_file_order_and_semantic_gates(tmp_path):
    rules = load(tmp_path,
        '  - {id: low, priority: 1, when: {mentions_bot: true}, decision: ignore, reason: low}\n'
        '  - {id: first, priority: 10, when: {mentions_bot: true}, decision: wake, reason: first}\n'
        '  - {id: second, priority: 10, when: {mentions_bot: true}, decision: ignore, reason: second}\n'
        '  - {id: semantic, semantic: true, when: {scope_kind: group}, decision: wake, reason: maybe}\n')
    assert evaluate_mechanical(rules, {'mentions_bot': True}).id == 'first'
    assert [rule.id for rule in candidate_semantic_rules(rules, {'scope_kind': 'group'})] == ['semantic']
    assert candidate_semantic_rules(rules, {'scope_kind': 'private'}) == ()


@pytest.mark.parametrize('entries', [
    '  - {id: none, decision: wake, reason: test}',
    '  - {id: a, decision: wake, reason: test}\n  - {id: a, decision: ignore, reason: test}',
    '  - {id: a, when: {unknown: true}, decision: wake, reason: test}',
    '  - {id: a, when: {text: {regex: x}}, decision: wake, reason: test}',
    '  - {id: a, when: {text: {contains_any: x}}, decision: wake, reason: test}',
])
def test_invalid_rules_rejected(tmp_path, entries):
    with pytest.raises(RuleValidationError):
        load(tmp_path, entries)


def test_missing_fact_does_not_match_ne(tmp_path):
    rules = load(tmp_path, '  - {id: a, when: {count: {ne: 1}}, decision: wake, reason: test}')
    assert evaluate_mechanical(rules, {}) is None


def test_composite_rules_and_membership(tmp_path):
    rules = load(tmp_path, '  - id: a\n    decision: wake\n    reason: test\n    when:\n'
        '      all:\n        - {scope_kind: {in: [group, private]}}\n'
        '        - {count: {gte: 1, lte: 3}}\n'
        '        - {not: {text: {contains_any: [bad]}}}\n')
    assert evaluate_mechanical(rules, {'scope_kind': 'group', 'count': 2, 'text': 'hello'}).id == 'a'
    assert evaluate_mechanical(rules, {'scope_kind': 'group', 'count': 4, 'text': 'hello'}) is None
