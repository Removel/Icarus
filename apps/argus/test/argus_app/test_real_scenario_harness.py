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


def test_discovery_fixtures_keep_original_initials_and_cover_routing_boundaries():
    from apps.argus.test import real_scenarios
    assert callable(getattr(real_scenarios, 'discovery_cases', None)), 'discovery cases are missing'
    cases = real_scenarios.discovery_cases()
    assert [c['text'] for c in cases[:6]] == [s.initial for s in scenarios()]
    assert [c['expected_action'] for c in cases[:6]] == ['new'] * 6
    assert {'new', 'existing', 'ignore', 'defer'} <= {c['expected_action'] for c in cases}


def test_discovery_cli_parses_explicit_budgets_without_calling_models(tmp_path, monkeypatch):
    import sys
    module = runner()
    captured = []
    async def execute(args):
        captured.append(args)
        return True
    monkeypatch.setattr(module, 'execute', execute)
    monkeypatch.setattr(sys, 'argv', ['manual_real_scenarios', '--authorize-real-model',
        '--mode', 'discovery', '--fallback-model', 'perception', '--discovery-initial-only',
        '--max-semantic-requests', '6', '--max-fallback-requests', '6', '--output', str(tmp_path / 'run')])
    assert module.main() == 0
    assert captured[0].mode == 'discovery'
    assert captured[0].fallback_model == 'perception'
    assert captured[0].max_fallback_requests == 6
    assert captured[0].discovery_initial_only is True


def discovery_case():
    return {'scenario': 'work_review', 'category': 'initial', 'text': '请跟进接口评审',
            'candidates': [], 'expected_action': 'new'}


async def test_discovery_report_preserves_primary_score_and_fallback_call_count(tmp_path):
    module = runner()
    assert callable(getattr(module, 'run_discovery_matrix', None)), 'discovery runner is missing'
    from argus_app.contracts.models import DiscoveryDecision
    calls = []
    async def classify(args):
        return {'match': False, 'score': .64, 'selected_route': 'new', 'defer_reason': 'low_confidence',
                'reason': 'typed discovery judgement', 'discovery': {'action': 'defer'}}
    async def fallback(text, candidates, *, criterion, context):
        calls.append((text, candidates))
        return DiscoveryDecision(action='new', reason='explicit request')
    report = await module.run_discovery_matrix(classify, fallback, tmp_path, selected_cases=[discovery_case()])
    assert report['correct'] == 1 and report['all_expected'] is True
    assert report['attempts'] == 1 and report['fallback_attempts'] == 1
    assert report['cases'][0]['selected_route'] == 'new'
    assert report['cases'][0]['native_confidence'] == .64
    assert report['cases'][0]['final_decision']['action'] == 'new'
    assert report['durable_workflow_executed'] is False
    assert len(calls) == 1


async def test_discovery_high_confidence_does_not_claim_live_fallback_validation(tmp_path):
    module = runner()
    assert callable(getattr(module, 'run_discovery_matrix', None)), 'discovery runner is missing'
    async def classify(args):
        return {'match': True, 'score': .9, 'selected_route': 'new', 'reason': 'confident', 'discovery': {'action': 'new'}}
    async def fallback(text, candidates, *, criterion, context):
        raise AssertionError('must not call fallback')
    report = await module.run_discovery_matrix(classify, fallback, tmp_path, selected_cases=[discovery_case()])
    assert report['all_expected'] is True
    assert report['fallback_attempts'] == 0 and report['fallback_exercised'] is False


async def test_discovery_fallback_budget_stops_before_second_request(tmp_path):
    module = runner()
    assert callable(getattr(module, 'run_discovery_matrix', None)), 'discovery runner is missing'
    from argus_app.contracts.models import DiscoveryDecision
    calls = []
    async def classify(args):
        return {'match': False, 'score': .4, 'selected_route': 'new', 'defer_reason': 'low_confidence',
                'reason': 'uncertain', 'discovery': {'action': 'defer'}}
    async def fallback(text, candidates, *, criterion, context):
        calls.append(text)
        return DiscoveryDecision(action='new', reason='explicit')
    report = await module.run_discovery_matrix(classify, fallback, tmp_path, fallback_maximum=1,
        selected_cases=[discovery_case(), discovery_case()])
    assert len(calls) == 1 and report['fallback_attempts'] == 1
    assert report['all_expected'] is False
    assert report['stopped_reason'] == 'fallback request budget exhausted'


async def test_discovery_fallback_error_stops_and_does_not_save_error_body(tmp_path):
    module = runner()
    assert callable(getattr(module, 'run_discovery_matrix', None)), 'discovery runner is missing'
    async def classify(args):
        return {'match': False, 'score': .4, 'defer_reason': 'low_confidence', 'reason': 'uncertain',
                'discovery': {'action': 'defer'}}
    async def fallback(text, candidates, *, criterion, context):
        raise RuntimeError('synthetic credential must not appear')
    report = await module.run_discovery_matrix(classify, fallback, tmp_path,
        selected_cases=[discovery_case(), discovery_case()])
    assert report['attempts'] == report['fallback_attempts'] == 1
    assert report['stopped_reason'] == 'RuntimeError'
    assert 'synthetic credential' not in (tmp_path / 'discovery-summary.json').read_text()
