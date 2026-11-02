"""Opt-in paid synthetic acceptance: JEV matrix, then real Argus/Gateway/Agent rounds.

Run from repository root with python -m apps.argus.test.manual_real_scenarios.
Never loaded by pytest. No real platform account, history or outward business actions.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time

from apps.argus.test.model_budget import ModelBudget
from apps.argus.test.real_scenarios import semantic_cases


def save(path, data):
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    os.chmod(temporary, 0o600)
    temporary.replace(path)


async def run_semantic_matrix(classify, output: Path, *, maximum=120):
    """The actual provider callback is injected; tests never contact a paid endpoint."""
    budget = ModelBudget(semantic=maximum)
    report = {'started_at': datetime.now(timezone.utc).isoformat(), 'ready': False,
              'attempts': 0, 'correct': 0, 'all_expected': False, 'cases': [], 'stopped_reason': None}
    for case in semantic_cases():
        started = time.monotonic()
        try:
            budget.consume('semantic')
        except ValueError as exc:
            report['stopped_reason'] = str(exc)
            break
        report['attempts'] = budget.counts['semantic']
        try:
            value = await classify({'text': case['text'], 'criterion': case['criterion'],
                                    'context': '合成验收文本；只分类，不执行业务动作。'})
            if type(value.get('match')) is not bool:
                raise ValueError('invalid semantic result')
            actual = value['match']
            report['cases'].append({**case, 'actual': actual, 'score': value.get('score'),
                                     'correct': actual == case['expected'],
                                     'elapsed_seconds': round(time.monotonic() - started, 3)})
            report['correct'] += int(actual == case['expected'])
            report['ready'] = True
        except Exception as exc:
            # Error bodies/headers can contain secrets; persist only class and HTTP status.
            report['cases'].append({**case, 'error_type': type(exc).__name__,
                                     'http_status': getattr(exc, 'status_code', None),
                                     'elapsed_seconds': round(time.monotonic() - started, 3)})
            report['stopped_reason'] = type(exc).__name__
            report['ready'] = False
            break
        finally:
            save(output / 'semantic-summary.json', report)
    report['all_expected'] = len(report['cases']) == len(semantic_cases()) and report['correct'] == len(semantic_cases())
    report['finished_at'] = datetime.now(timezone.utc).isoformat()
    save(output / 'semantic-summary.json', report)
    return report


def pipeline_passed(report):
    cases = report.get('scenarios', [])
    return bool(cases) and not report.get('stopped_reason') and all(
        case.get('initial_result', {}).get('settled') is True
        and case.get('followup_result', {}).get('completed') is True for case in cases)


async def execute(args):
    from argus_app.model_provider.semantic import configured_backend
    from apps.argus.test.real_scenarios import scenarios
    key = os.getenv('ARGUS_JEV_API_KEY', '')
    if not key:
        raise ValueError('ARGUS_JEV_API_KEY is required')
    backend, owner = configured_backend(key, os.getenv('ARGUS_JEV_BASE_URL'), os.getenv('ARGUS_JEV_MODEL'))
    try:
        matrix = await run_semantic_matrix(backend, args.output, maximum=args.max_semantic_requests)
        route = {'base_url': owner.base_url, 'model': owner.model}
    finally:
        await owner.aclose()
    summary = {'route': route, 'proxy': args.proxy or 'inherited environment',
               'semantic_attempts': matrix['attempts'], 'semantic_correct': matrix['correct'],
               'semantic_total': len(semantic_cases()), 'full_pipeline_executed': False,
               'budgets': {'agent': args.max_agent_requests, 'per_scenario': args.max_scenario_requests,
                           'semantic_total': args.max_semantic_requests, 'scenario_seconds': args.scenario_timeout},
               'stopped_reason': matrix['stopped_reason']}
    if args.mode == 'full' and matrix['ready'] and matrix['all_expected']:
        remaining = args.max_semantic_requests - matrix['attempts']
        if remaining < 1:
            summary['stopped_reason'] = 'No semantic budget remains for full pipeline'
        else:
            from apps.argus.test.manual_real_model import run
            chosen = [s for s in scenarios() if not args.scenario or s.name in args.scenario]
            full_dir = args.output / 'pipeline'
            full_dir.mkdir(mode=0o700)
            await run(full_dir, max_agent_requests=args.max_agent_requests,
                      max_scenario_requests=args.max_scenario_requests, max_semantic_requests=remaining,
                      max_safety_requests=16, scenario_timeout=args.scenario_timeout, selected_scenarios=chosen)
            summary['full_pipeline_executed'] = True
            summary['pipeline_summary'] = 'pipeline/summary.json'
            pipeline = json.loads((full_dir / 'summary.json').read_text())
            summary['pipeline_passed'] = pipeline_passed(pipeline)
            summary['pipeline_scenarios'] = [{'name': case['name'],
                'initial_settled': case.get('initial_result', {}).get('settled', False),
                'followup_completed': case.get('followup_result', {}).get('completed', False)}
                for case in pipeline['scenarios']]
            if not summary['pipeline_passed']:
                summary['stopped_reason'] = pipeline.get('stopped_reason') or 'One or more business scenarios failed'
    elif args.mode == 'full' and not summary['stopped_reason']:
        summary['stopped_reason'] = 'Semantic quality gate failed; full paid pipeline not started'
    save(args.output / 'acceptance-summary.json', summary)
    print(json.dumps(summary, ensure_ascii=False))
    return matrix['all_expected'] and not summary['stopped_reason']


def main():
    os.umask(0o077)
    from agent_app.config.environment import load_icarus_environment
    from apps.argus.test.real_scenarios import scenarios
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--authorize-real-model', action='store_true', required=True)
    parser.add_argument('--output', type=Path, required=True, help='New private directory; must not already exist')
    parser.add_argument('--mode', choices=('semantic', 'full'), default='full')
    parser.add_argument('--proxy', help='HTTP proxy URL used only in this process, e.g. http://127.0.0.1:7890')
    parser.add_argument('--scenario', action='append', choices=[s.name for s in scenarios()])
    parser.add_argument('--max-agent-requests', type=int, default=240)
    parser.add_argument('--max-scenario-requests', type=int, default=80)
    parser.add_argument('--max-semantic-requests', type=int, default=120)
    parser.add_argument('--scenario-timeout', type=float, default=600)
    args = parser.parse_args()
    if args.scenario_timeout <= 0:
        parser.error('--scenario-timeout must be positive')
    ModelBudget(agent=args.max_agent_requests, scenario=args.max_scenario_requests, semantic=args.max_semantic_requests)
    load_icarus_environment()
    if args.proxy:
        from urllib.parse import urlsplit
        parsed = urlsplit(args.proxy)
        if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password:
            parser.error('--proxy must be an HTTP(S) proxy without embedded credentials')
        for name in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy', 'all_proxy'):
            os.environ[name] = args.proxy
        os.environ['NO_PROXY'] = os.environ['no_proxy'] = 'localhost,127.0.0.1,::1'
    args.output = args.output.expanduser().resolve()
    args.output.mkdir(mode=0o700, parents=True, exist_ok=False)
    try:
        return 0 if asyncio.run(execute(args)) else 1
    except Exception as exc:
        save(args.output / 'harness-error.json', {'error_type': type(exc).__name__})
        print(json.dumps({'error_type': type(exc).__name__, 'output': str(args.output)}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
