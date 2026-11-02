"""Offline harness checks; no real credentials or provider calls in pytest."""
import importlib
from pathlib import Path

import pytest

from apps.argus.test.real_scenarios import scenarios, semantic_cases


def test_business_scenarios_are_distinct_and_labels_match_plain_requirements():
    cases = scenarios()
    assert len(cases) == 6
    assert len({x.name for x in cases}) == 6
    assert {x.platform for x in cases} == {'qq', 'feishu'}
    assert len(semantic_cases()) == 24
    for case in cases:
        assert case.initial and case.followup and case.criterion
        assert not any(tool in case.initial for tool in ('argus_commit', 'argus_confirm', 'tool_search', 'def main'))


def runner():
    try:
        module = importlib.import_module('apps.argus.test.manual_real_scenarios')
    except ModuleNotFoundError:
        module = None
    assert module is not None, 'multi-scenario preflight runner is required'
    return module


async def test_permission_denied_preflight_stops_before_scenario_calls(tmp_path):
    module = runner()
    calls = []
    class TypeSafePermissionDeniedError(Exception):
        pass
    async def classify(args):
        calls.append(args)
        raise TypeSafePermissionDeniedError('synthetic error; do not log this body')
    result = await module.run_semantic_matrix(classify, tmp_path, maximum=120)
    assert len(calls) == 1
    assert result['ready'] is False
    assert result['attempts'] == 1
    assert result['stopped_reason'] == 'TypeSafePermissionDeniedError'
    assert 'synthetic error' not in (tmp_path / 'semantic-summary.json').read_text()


async def test_semantic_matrix_records_wrong_answers_as_failures_not_transport_success(tmp_path):
    module = runner()
    async def classify(args):
        return {'match': True, 'score': .9}
    result = await module.run_semantic_matrix(classify, tmp_path, maximum=120)
    assert result['attempts'] == 24
    assert result['correct'] == 6
    assert result['ready'] is True  # connected; not a business-quality success claim
    assert result['all_expected'] is False


async def test_semantic_matrix_budget_is_checked_before_upstream(tmp_path):
    module = runner()
    calls = []
    async def classify(args):
        calls.append(args)
        return {'match': False, 'score': .1}
    result = await module.run_semantic_matrix(classify, tmp_path, maximum=2)
    assert len(calls) == 2
    assert result['attempts'] == 2
    assert result['all_expected'] is False
    assert result['stopped_reason'] == 'semantic request budget exhausted'


def test_full_pipeline_success_requires_all_scenarios_committed():
    module = runner()
    assert hasattr(module, 'pipeline_passed'), 'HTTP/model success is not business completion'
    assert module.pipeline_passed({'scenarios': []}) is False
    assert module.pipeline_passed({'scenarios': [{'initial_result': {'settled': True},
                                                'followup_result': {'completed': False}}]}) is False
    assert module.pipeline_passed({'scenarios': [{'initial_result': {'settled': True},
                                                'followup_result': {'completed': True}}]}) is True
    assert module.pipeline_passed({'stopped_reason': 'exhausted', 'scenarios': [
        {'initial_result': {'settled': True}, 'followup_result': {'completed': True}}]}) is False
