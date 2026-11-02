"""Opt-in paid-model acceptance, synthetic ingress only; never loaded by pytest.

Run with --authorize-real-model --output <new-private-directory>.
A local recorder forwards genuine model calls with a strict request budget. Only
synthetic prompts, responses and application state are saved; no credentials/headers.
"""
from __future__ import annotations

import argparse
import asyncio
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import socket
import threading
import time
from uuid import uuid4

import httpx
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from agent_app.application import AgentRuntime
from agent_app.config.config_loader import get_config
from icarus.core.identity import SessionIdentity
from apps.gateway.src.app import create_app as gateway_app
from apps.gateway.test.test_runtime_e2e import config_for
from apps.argus.test.argus_app.test_closed_loop import listener, serve
from argus_app.application import create_app
from argus_app.config import ArgusConfig
from argus_app.infrastructure.store import Store, observed_items
from sqlalchemy import select
from apps.argus.test.model_budget import ModelBudget
from argus_app.model_provider.semantic import configured_backend


class Recorder(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    budget = 120
    accounting = ModelBudget()
    calls = []
    lock = threading.Lock()

    def log_message(self, *_args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))))
        if not self.path.endswith('/chat/completions'):
            data = b'{"results":[]}'
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        with self.lock:
            index = len(self.calls)
            try:
                self.accounting.consume('agent')
            except ValueError as exc:
                self.send_error(429, str(exc))
                return
            record = {'index': index + 1, 'timestamp': datetime.now(timezone.utc).isoformat(),
                      'request': body, 'response_chunks': [], 'usage': None}
            self.calls.append(record)
        start = time.monotonic()
        try:
            # Authentication stays only in this forwarding client, never in records.
            with httpx.Client(timeout=90) as client:
                with client.stream('POST', self.upstream.rstrip('/') + '/chat/completions',
                    headers={'Authorization': 'Bearer ' + self.api_key}, json=body) as response:
                    record['status_code'] = response.status_code
                    if response.status_code != 200:
                        data = response.read()
                        record['error_body'] = 'upstream request failed; body omitted to avoid credential disclosure'
                        if response.status_code in {401, 403}:
                            self.accounting.stop('agent authentication_failed')
                        self.send_response(response.status_code)
                        self.send_header('Content-Type', 'application/json')
                        self.send_header('Content-Length', str(len(data)))
                        self.end_headers()
                        self.wfile.write(data)
                    else:
                        self.send_response(200)
                        self.send_header('Content-Type', response.headers.get('Content-Type', 'text/event-stream'))
                        self.send_header('Connection', 'close')
                        self.end_headers()
                        self.close_connection = True
                        for line in response.iter_lines():
                            if line.startswith('data: ') and line[6:] != '[DONE]':
                                try:
                                    chunk = json.loads(line[6:])
                                    record['response_chunks'].append(chunk)
                                    if chunk.get('usage'):
                                        record['usage'] = chunk['usage']
                                except ValueError:
                                    pass
                            self.wfile.write((line + '\n').encode())
                            self.wfile.flush()
        except Exception as exc:
            record['error_type'] = type(exc).__name__
        finally:
            record['elapsed_seconds'] = round(time.monotonic() - start, 3)
            (self.output / f'model-{index + 1:02}.json').write_text(json.dumps(record, ensure_ascii=False, indent=2))


@contextmanager
def model_recorder(config, output):
    Recorder.calls = []
    Recorder.upstream = config.openai_base_url
    Recorder.api_key = config.openai_api_key
    Recorder.output = output
    server = ThreadingHTTPServer(('127.0.0.1', 0), Recorder)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        Recorder.api_key = ''


async def run(output: Path, *, max_agent_requests=120, max_scenario_requests=50,
              max_semantic_requests=64, max_safety_requests=16, scenario_timeout=600,
              selected_scenarios=None):
    accounting = ModelBudget(agent=max_agent_requests, scenario=max_scenario_requests,
                             semantic=max_semantic_requests, safety=max_safety_requests)
    Recorder.accounting, Recorder.budget = accounting, max_agent_requests
    key = os.getenv('ARGUS_JEV_API_KEY', '')
    if not key:
        raise ValueError('ARGUS_JEV_API_KEY is required for genuine configured semantic validation')
    owner = None
    original = os.environ['ARGUS_TEST_DATABASE_URL']
    url = make_url(original)
    if url.drivername != 'postgresql+asyncpg' or not (url.database or '').endswith('_test'):
        raise ValueError('Dedicated disposable *_test PostgreSQL required')
    actual = get_config()
    if not actual.openai_api_key:
        raise ValueError('Existing configured model credentials are unavailable')
    db_name = 'argus_real_' + uuid4().hex + '_test'
    admin = create_async_engine(original, isolation_level='AUTOCOMMIT')
    async with admin.connect() as connection:
        await connection.execute(text(f'CREATE DATABASE "{db_name}"'))
    isolated = url.set(database=db_name).render_as_string(hide_password=False)
    store = Store(isolated)
    gs, ars = listener(), listener()
    gateway_url = f'http://127.0.0.1:{gs.getsockname()[1]}'
    argus_url = f'http://127.0.0.1:{ars.getsockname()[1]}'
    os.environ.update(ARGUS_GATEWAY_TOKEN='real-smoke-gateway', ARGUS_AGENT_API_TOKEN='real-smoke-callback',
                      ARGUS_WORKSPACE_PATH=str(output / 'workspace'))
    summary = {'started_at': datetime.now(timezone.utc).isoformat(), 'model': actual.get_model(actual.model_roles.agent).model_name,
               'scope': 'synthetic QQ/Feishu ingress; genuine paid model; real PG/Host/Agent/HTTP',
               'request_budget': Recorder.budget, 'scenarios': [], 'semantic_calls': []}
    try:
        with model_recorder(actual, output) as provider_port:
            config = config_for(output / 'agent', provider_port)
            for item in config.model_settings:
                item.model_name = actual.get_model(actual.model_roles.agent).model_name
                item.max_tokens = 3072
                item.default_think_level = type(item.default_think_level)('low')
            config.agent.max_steps = 12
            runtime = AgentRuntime(config_loader=lambda: config)
            gateway = gateway_app(runtime)
            backend, owner = configured_backend(key, os.getenv('ARGUS_JEV_BASE_URL'), os.getenv('ARGUS_JEV_MODEL'))
            async def semantic(arguments):
                # Genuine configured typed JEV, not a substitute deep-model endpoint.
                accounting.consume('semantic')
                started = time.monotonic()
                try:
                    result = await backend(arguments)
                except Exception as exc:
                    if type(exc).__name__ in {'TypeSafeAuthenticationError', 'TypeSafePermissionDeniedError'}:
                        accounting.stop('semantic authentication_failed')
                    raise
                summary['semantic_calls'].append({'input': arguments, 'output': result,
                                                   'elapsed_seconds': round(time.monotonic() - started, 3)})
                return result
            config_argus = ArgusConfig(database_url=SecretStr(isolated), data_dir=output / 'argus',
                agent_token=SecretStr('real-smoke-callback'), gateway_token=SecretStr('real-smoke-gateway'),
                gateway_url=gateway_url, public_url=argus_url, qq_token=SecretStr('synthetic-qq-secret'),
                feishu_verification_token=SecretStr('synthetic-feishu-token'), scan_interval=.3)
            argus = create_app(config_argus, store=store, semantic=semantic)
            async with serve(gateway, gs), serve(argus, ars):
                async with httpx.AsyncClient(base_url=argus_url, timeout=20) as client:
                    async def send(platform, source, message, request_id):
                        if platform == 'qq':
                            payload = {'post_type': 'message', 'self_id': 10001, 'message_type': 'group',
                                       'group_id': source, 'user_id': 20001, 'message_id': request_id, 'raw_message': message}
                            raw = json.dumps(payload).encode()
                            signature = 'sha1=' + hmac.new(b'synthetic-qq-secret', raw, hashlib.sha1).hexdigest()
                            response = await client.post('/protocols/qq', content=raw, headers={'X-Signature': signature})
                        else:
                            payload = {'schema': '2.0', 'header': {'event_type': 'im.message.receive_v1', 'tenant_key': 'synthetic-tenant',
                                       'token': 'synthetic-feishu-token', 'event_id': request_id}, 'event': {'sender': {'sender_id': {'open_id': 'ou_synthetic'}},
                                       'message': {'chat_id': source, 'message_id': request_id, 'message_type': 'text',
                                                   'content': json.dumps({'text': message}, ensure_ascii=False)}}}
                            response = await client.post('/protocols/feishu', json=payload)
                        response.raise_for_status()
                        return {'platform': platform, 'message_id': request_id, 'text': message,
                                'http_status': response.status_code, 'observation_id': response.json()['observation_id']}
                    async def settle(event_id, timeout=scenario_timeout):
                        start = time.monotonic()
                        last = None
                        while time.monotonic() - start < timeout:
                            if accounting.stopped_reason:
                                return {'settled': False, 'reason': accounting.stopped_reason}
                            item = await store.observation(event_id)
                            channel_id = item.detail.get('discovery', {}).get('channel_id')
                            if channel_id:
                                ch = await store.channel(channel_id)
                                last = ch.model_dump()
                                if ch.runtime.get('round') is None and not ch.runtime.get('pending'):
                                    return {'settled': True, 'elapsed_seconds': round(time.monotonic() - start, 2), 'channel': last}
                                if ch.runtime.get('round') is None and ch.runtime.get('pending') and all(
                                    entry.get('delivery_attempt_count', 0) >= 3 for entry in ch.runtime['pending'].values()):
                                    return {'settled': False, 'reason': 'delivery_exhausted',
                                            'elapsed_seconds': round(time.monotonic() - start, 2), 'channel': last}
                            await asyncio.sleep(.3)
                        return {'settled': False, 'elapsed_seconds': round(time.monotonic() - start, 2), 'channel': last}
                    from apps.argus.test.real_scenarios import scenarios as business_scenarios
                    cases_to_run = selected_scenarios if selected_scenarios is not None else business_scenarios()
                    for scenario in cases_to_run:
                        name, platform, source, message = scenario.name, scenario.platform, scenario.source, scenario.initial
                        followup = (scenario.followup_platform, scenario.followup_source, scenario.followup)
                        accounting.new_scenario()
                        case = {'name': name, 'initial': await send(platform, source, message, name + '-initial')}
                        case['initial_result'] = await settle(case['initial']['observation_id'])
                        summary['scenarios'].append(case)
                        (output / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2))
                        if not case['initial_result']['settled']:
                            case['failure'] = 'Initial business round did not finish'
                            summary['stopped_reason'] = accounting.stopped_reason or 'Unsettled round; stop to avoid overlapping paid tasks'
                            break
                        channel_id = case['initial_result']['channel']['id']
                        initial_version = case['initial_result']['channel']['version']
                        if initial_version == 1:
                            case['failure'] = 'Agent closed initialization without publishing a next-stage rule'
                            continue
                        case['followup'] = await send(*followup, name + '-followup')
                        start = time.monotonic()
                        ch = await store.channel(channel_id)
                        while time.monotonic() - start < scenario_timeout:
                            if accounting.stopped_reason:
                                break
                            ch = await store.channel(channel_id)
                            if ch.lifecycle == 'completed' and ch.runtime.get('round') is None:
                                break
                            await asyncio.sleep(.3)
                        case['followup_result'] = {'channel': ch.model_dump(), 'completed': ch.lifecycle == 'completed' and ch.runtime.get('round') is None,
                                                   'elapsed_seconds': round(time.monotonic() - start, 2)}
                        if not case['followup_result']['completed']:
                            case['failure'] = 'Followup business commit did not complete'
                            summary['stopped_reason'] = accounting.stopped_reason or 'Unsettled followup; stop before next scenario'
                            break
                        (output / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2))
                    # Save facts, control results, rule code and actual Agent journal.
                    async with store.engine.connect() as conn:
                        facts = (await conn.execute(select(observed_items).order_by(observed_items.c.position))).mappings().all()
                    (output / 'events.json').write_text(json.dumps([dict(row) for row in facts], ensure_ascii=False, indent=2))
                    for channel in await store.channels():
                        (output / ('channel-' + channel.id + '.json')).write_text(channel.model_dump_json(indent=2))
                        if channel.rule_path:
                            (output / ('rule-' + channel.id + '.py')).write_text(argus.state.rule_files.read(channel.rule_path, channel.rule_hash))
                    workspace = Path(os.environ['ARGUS_WORKSPACE_PATH'])
                    root_identity = SessionIdentity.create(workspace)
                    for session_id in await runtime.session_store.list_session_ids(root_identity.workspace_key):
                        records, _ = await runtime.session_store.read_updates(SessionIdentity.create(workspace, session_id))
                        (output / ('agent-' + session_id + '.json')).write_text(json.dumps([
                            {'type': row.type, 'task_id': row.task_id, 'payload': dict(row.payload)} for row in records], ensure_ascii=False, indent=2))
        summary['model_request_count'] = len(Recorder.calls)
        summary['request_counts'] = dict(accounting.counts)
        summary['budget_stopped_reason'] = accounting.stopped_reason
        summary['semantic_route'] = {'base_url': owner.base_url, 'model': owner.model}
        summary['usage'] = [r['usage'] for r in Recorder.calls if r.get('usage')]
        summary['finished_at'] = datetime.now(timezone.utc).isoformat()
        (output / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2))
        print(json.dumps({'output': str(output), 'model_request_count': len(Recorder.calls),
                          'scenarios': [{'name': c['name'], 'initial_settled': c['initial_result']['settled'],
                                         'followup_completed': c.get('followup_result', {}).get('completed')} for c in summary['scenarios']],
                          'stopped_reason': summary.get('stopped_reason')}, ensure_ascii=False))
    finally:
        if owner is not None:
            await owner.aclose()
        await store.close()
        async with admin.connect() as connection:
            await connection.execute(text(f'DROP DATABASE "{db_name}" WITH (FORCE)'))
        await admin.dispose()


if __name__ == '__main__':
    os.umask(0o077)
    parser = argparse.ArgumentParser()
    parser.add_argument('--authorize-real-model', action='store_true', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--max-agent-requests', type=int, default=120)
    parser.add_argument('--max-scenario-requests', type=int, default=50)
    parser.add_argument('--max-semantic-requests', type=int, default=64)
    parser.add_argument('--max-safety-requests', type=int, default=16)
    parser.add_argument('--scenario-timeout', type=float, default=600)
    args = parser.parse_args()
    if args.scenario_timeout <= 0:
        parser.error('--scenario-timeout must be positive')
    from agent_app.config.environment import load_icarus_environment
    load_icarus_environment()
    args.output.mkdir(parents=True, exist_ok=False)
    os.chmod(args.output, 0o700)
    asyncio.run(run(args.output.resolve(), max_agent_requests=args.max_agent_requests,
                    max_scenario_requests=args.max_scenario_requests, max_semantic_requests=args.max_semantic_requests,
                    max_safety_requests=args.max_safety_requests, scenario_timeout=args.scenario_timeout))
