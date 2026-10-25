"""Offline v2 acceptance: real Argus/PG -> WebSocket -> Agent/SQLite/ReAct.

A blocked local model call is the safe-edge gate. The only semantic substitute is
JEV's typed classification answer; no Agent, inbox, delivery or report is faked.
"""

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
import json
import os
from pathlib import Path
import socket

from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
import httpx
import pytest
from typesafe_sdk import ChoiceAnswer, SystemOneResponse, Usage
import uvicorn

from apps.argus.src.main import create_app
from apps.argus.src.core.config import ArgusConfig
from apps.argus.src.common.clients.gateway_agent_client import GatewayAgentClient
from apps.argus.src.common.clients.jev_client import JevClient
from apps.argus.src.common.services.judgement_service import JudgementService
from apps.argus.src.common.services.real_wake_service import RealWakeService
from apps.argus.src.common.services.rule_service import load_rule_set
from apps.argus.src.common.services.session_routing_service import SessionRoutingService
from apps.argus.src.common.tasks.observation_scheduler import ObservationScheduler
from apps.argus.src.surfaces.qq.services.fact_service import QQFactService
from apps.argus.test.factories import draft_factory


A1 = 'A1-LUNAR: inspect lunar navigation drift; compare inertial calibration and star tracker.'
A2 = 'A2-UNREAD-SENTINEL: lunar star-tracker calibration is now 17 arcseconds.'
B = 'B-UNRELATED-SENTINEL: plan the greenhouse irrigation and tomato harvest.'
A3 = 'A3-RESUMED-SENTINEL: revisit lunar navigation drift with a second calibration.'
NOTICE = '当前语境有新的待读消息，可在合适时通过 argus_wait 读取。'
SCOPE = {'source': 'qq', 'account_id': '123', 'chat_kind': 'group', 'external_id': '456'}
ROOT = Path(__file__).resolve().parents[4]


@asynccontextmanager
async def tcp_app(app):
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    sock.listen(128)
    server = uvicorn.Server(uvicorn.Config(app, log_level='error'))
    ready = asyncio.Event()
    startup = server.startup

    async def start(*args, **kwargs):
        await startup(*args, **kwargs)
        ready.set()

    server.startup = start
    serving = asyncio.create_task(server.serve(sockets=[sock]))
    try:
        await asyncio.wait_for(ready.wait(), 10)
        yield f'http://127.0.0.1:{sock.getsockname()[1]}'
    finally:
        server.should_exit = True
        await asyncio.wait_for(serving, 10)
        sock.close()


class ScriptedProvider:
    """OpenAI-compatible local SSE; captures the actual serialized model inputs."""

    def __init__(self):
        self.app = FastAPI()
        self.inputs, self.outcomes = [], []
        self.pending_report_seen = asyncio.Event()
        self.initial_seen, self.initial_release = asyncio.Event(), asyncio.Event()
        self.blocked, self.release, self.original_returned = (asyncio.Event() for _ in range(3))
        self.cancelled = False
        self.stage, self.last_call, self.last_operation = 1, None, None
        self.stages, self.retries, self.failures = [], {}, []
        self.gateway_url = None
        self.last_delivery_call = None
        self.app.post('/v1/chat/completions')(self.complete)
        self.app.post('/{path:path}')(self.auxiliary)

    async def auxiliary(self, request: Request):
        return {'results': []}

    async def complete(self, request: Request):
        body = await request.json()
        self.inputs.append(body)
        tool_results = [message for message in body['messages'] if message.get('role') == 'tool']
        if self.last_call is not None:
            assert tool_results and tool_results[-1]['tool_call_id'] == self.last_call
            result = json.loads(tool_results[-1]['content'])
            self.outcomes.append({'stage': self.stage, 'result': result})
            if result.get('success') is True:
                if self.last_operation[0] == 'argus_report':
                    assert result['output']['disposition'] == 'accepted', result
                self.stage += 1
            elif result.get('error') == 'delivery_pending' and self.last_operation[0] == 'argus_report':
                self.pending_report_seen.set()
                self.retries[self.stage] = self.retries.get(self.stage, 0) + 1
                assert self.retries[self.stage] <= 3, 'delivery_pending retry budget exhausted'
                ref = self.last_operation[1]['items'][0]['ref']
                assert self.gateway_url is not None
                async with httpx.AsyncClient(timeout=20) as http:
                    response = await http.get(self.gateway_url + f'/fixture/granted/{self.last_delivery_call}/{ref[1:]}')
                    response.raise_for_status()
                    assert response.json() == {'granted': True}
            else:
                self.failures.append(result)
                raise AssertionError(f'Unexpected tool result: {result}')
        step = self.stage
        self.stages.append(step)
        script = {
            1: ('argus_read', {'ref': '#1'}),
            2: ('argus_report', {'items': [{'ref': '#1', 'status': 'handled'}]}),
            3: ('argus_wait', {'timeout_seconds': 0}),
            4: ('argus_read', {'ref': '#2'}),
            5: ('argus_report', {'items': [{'ref': '#2', 'status': 'handled'}]}),
            7: ('argus_read', {'ref': '#1'}),
            8: ('argus_report', {'items': [{'ref': '#1', 'status': 'no_action'}]}),
            10: ('argus_read', {'ref': '#3'}),
            11: ('argus_report', {'items': [{'ref': '#3', 'status': 'handled'}]}),
        }

        if step in script:
            self.last_operation = script[step]
            self.last_call = f'local-{len(self.inputs)}'
            if self.last_operation[0] in {'argus_read', 'argus_wait'}:
                self.last_delivery_call = self.last_call
        else:
            self.last_operation, self.last_call = None, None
            self.stage += 1

        async def stream():
            if step == 1:
                self.initial_seen.set()
                await asyncio.wait_for(self.initial_release.wait(), 20)
            if step == 2:
                self.blocked.set()
                try:
                    await asyncio.wait_for(self.release.wait(), 20)
                except asyncio.CancelledError:
                    self.cancelled = True
                    raise
            if step in script:
                name, arguments = script[step]
                delta = {'tool_calls': [{'index': 0, 'id': self.last_call, 'type': 'function',
                    'function': {'name': name, 'arguments': json.dumps(arguments)}}]}
                finish = 'tool_calls'
            else:
                delta, finish = {'content': 'done'}, 'stop'
            for value in ({'delta': delta, 'finish_reason': None}, {'delta': {}, 'finish_reason': finish}):
                chunk = {'id': f'local-{step}', 'object': 'chat.completion.chunk', 'created': 1,
                         'model': 'local', 'choices': [{'index': 0, **value}],
                         'usage': {'prompt_tokens': 100, 'completion_tokens': 20, 'total_tokens': 120}}
                yield 'data: ' + json.dumps(chunk) + '\n\n'
            yield 'data: [DONE]\n\n'
            if step == 2:
                self.original_returned.set()

        return StreamingResponse(stream(), media_type='text/event-stream')


@pytest.mark.parametrize('delay_delivery', [False, True], ids=['normal', 'delayed-ack'])
async def test_semantic_handoff_keeps_unread_body_out_until_agent_reads_and_reuses_session(database, tmp_path, monkeypatch, delay_delivery):
    monkeypatch.setenv('ARGUS_AGENT_API_TOKEN', 'fixture-data')
    monkeypatch.setenv('ARGUS_GATEWAY_TOKEN', 'fixture-control')
    monkeypatch.setenv('PYTHON_DOTENV_DISABLED', '1')
    for key in ('ICARUS_ENV_FILE', 'OPENAI_API_KEY', 'ANTHROPIC_API_KEY',
                'ICARUS_MEM0_LLM_API_KEY', 'ICARUS_OPENKB_LLM_API_KEY'):
        monkeypatch.delenv(key, raising=False)
    now = [datetime.now(UTC)]
    app = create_app(ArgusConfig(execution={'mode': 'gateway'}), database=database)
    initial_delivered = asyncio.Event()
    provider = ScriptedProvider()
    delivery_count = 0

    @app.middleware('http')
    async def observe_delivery(request, call_next):
        nonlocal delivery_count
        response = await call_next(request)
        if request.url.path.endswith('/deliver') and response.status_code == 200:
            delivery_count += 1
            if delay_delivery and delivery_count == 2:
                # The real DB transaction already committed; only its HTTP response
                # is held so the plugin must preserve its pending grant correctly.
                await asyncio.wait_for(provider.pending_report_seen.wait(), 15)
            initial_delivered.set()
        return response

    process, client, scheduler = None, None, None
    log_path = tmp_path / 'gateway.log'
    async with tcp_app(app) as argus_url, tcp_app(provider.app) as model_url:
        try:
            repo = app.state.repository
            repo.clock = lambda: now[0]
            app.state.handoff_service.repository.clock = lambda: now[0]
            workspace = tmp_path / 'background'
            workspace.mkdir()
            model = {'model_name': 'local', 'context_window': 128000, 'max_tokens': 200,
                     'temperature': 0, 'default_think_level': 'low'}
            config = {'openai_base_url': model_url + '/v1', 'anthropic_base_url': model_url,
                'openai_api_key': 'fixture', 'anthropic_api_key': 'fixture',
                'mcp_servers': {}, 'icarus_data_dir': str(tmp_path / 'agent-data'),
                'model_settings': {'thinking': model, 'perception': model},
                'argus': {'enabled': True, 'workspace': str(workspace), 'base_url': argus_url},
                'runtime': {'plugin_config': {
                    'memory': {'user_id': 'fixture', 'agent_id': 'fixture', 'endpoint': model_url},
                    'knowledge': {'knowledge_base': 'fixture', 'endpoint': model_url}}}}
            config_path = tmp_path / 'agent.json'
            config_path.write_text(json.dumps(config))
            probe = socket.socket()
            probe.bind(('127.0.0.1', 0))
            gateway_port = probe.getsockname()[1]
            probe.close()
            with log_path.open('w') as log:
                process = await asyncio.create_subprocess_exec(
                    str(ROOT / 'apps/gateway/.venv/bin/python'), '-m',
                    'apps.argus.test.app.semantic_handoff_host', str(config_path), str(gateway_port),
                    now[0].isoformat(), cwd=ROOT, env=dict(os.environ),
                    stdout=asyncio.subprocess.PIPE, stderr=log)
                line = await asyncio.wait_for(process.stdout.readline(), 15)
                assert line == b'semantic-fixture-ready\n', log_path.read_text()
            gateway_url = f'http://127.0.0.1:{gateway_port}'
            provider.gateway_url = gateway_url
            client = GatewayAgentClient(f'ws://127.0.0.1:{gateway_port}/argus/rpc',
                                        'fixture-control', timeout_seconds=10)
            routes = []

            async def answer(state, questions):
                candidates = state['candidates']
                routes.append(state)
                selected = {}
                for n, name in enumerate(questions):
                    text = state['items'][n]['text']
                    label = next((value['label'] for value in candidates
                                  if 'lunar' in value['summary']), None)
                    choice = label if text.startswith(('A2-', 'A3-')) and label else f'new_{n}'
                    selected[name] = ChoiceAnswer(choice=choice, confidence=1, probabilities={})
                return SystemOneResponse(model='fixture', usage=Usage(), answers=selected)

            surface = QQFactService()
            rules = load_rule_set(ROOT / 'apps/argus/rules/qq.yaml', known_facts=set(surface.known_facts))
            policy = app.state.handoff_service.policy
            wakes = RealWakeService(repo, client, policy=policy,
                handoff=app.state.handoff_service.repository, clock=lambda: now[0])
            scheduler = ObservationScheduler(repo, JudgementService(rules, surface, None), wakes,
                routing=SessionRoutingService(JevClient(answer=answer), policy=policy), clock=lambda: now[0])

            async def observe(text, message_id):
                return (await repo.upsert_revision(draft_factory(text, message_id=message_id,
                    sent_at=now[0], observed_at=now[0], strong_signal=True,
                    detail={'mentions_bot': True}, locator={'account_id': '123', 'chat_kind': 'group',
                        'external_id': '456', 'message_id': message_id, 'time': now[0].timestamp()},
                    sender_name='Lunar observer' if text != B else 'Greenhouse planner')))[0]

            async with httpx.AsyncClient(timeout=25) as http:
                a1 = await observe(A1, '1001')
                await scheduler.process_chat(a1.chat_id)
                try:
                    await asyncio.wait_for(provider.initial_seen.wait(), 15)
                except TimeoutError:
                    raise AssertionError({'wakes': [value.model_dump(mode='json') for value in await repo.list_wakes()],
                                          'provider_inputs': provider.inputs, 'gateway_log': log_path.read_text()}) from None
                wake_a = await repo.active_wake(a1.chat_id)
                await asyncio.wait_for(initial_delivered.wait(), 10)
                assert (await repo.get_item(a1.id)).exposed_wake_id == wake_a.id
                assert (await repo.get_item(a1.id)).status == 'delivered'
                provider.initial_release.set()
                await asyncio.wait_for(provider.blocked.wait(), 15)
                binding_a = wake_a.handoff['binding']
                assert wake_a.payload.protocol_version == 2
                assert (await repo.get_item(a1.id)).exposed_wake_id == wake_a.id
                first = json.dumps(provider.inputs[0], ensure_ascii=False)
                assert A1 in first and '#1' in first and 'Lunar observer' in first
                assert all(value in first for value in ('account_id', 'external_id', 'sent_at', 'work'))
                assert str(a1.id) not in first and binding_a['binding_id'] not in first
                now[0] += timedelta(seconds=1)
                a2, b = await observe(A2, '1002'), await observe(B, '1003')
                await scheduler.process_chat(a1.chat_id)
                saved_a2, saved_b = await repo.get_item(a2.id), await repo.get_item(b.id)
                assert saved_a2.detail['argus_session_id'] == binding_a['session_id']
                assert saved_a2.wake_id == wake_a.id and saved_a2.exposed_wake_id is None
                waiting = await repo.waiting_wakes(a1.chat_id)
                assert len(waiting) == 1 and waiting[0].id == saved_b.wake_id
                assert waiting[0].payload.target_session_id is None
                assert 'binding' not in waiting[0].handoff
                for _ in range(3):
                    assert (await client.notify(wake_a.id, binding_a['session_id']))['accepted']
                response = await http.get(gateway_url + '/fixture/notice')
                response.raise_for_status()
                assert response.json()['notices'] == [{'task_id': binding_a['task_id'], 'text': NOTICE}]
                assert len(provider.inputs) == 2 and not provider.cancelled
                assert all(A2 not in json.dumps(value) and B not in json.dumps(value) for value in provider.inputs)
                provider.release.set()
                await asyncio.wait_for(provider.original_returned.wait(), 10)
                response = await http.get(gateway_url + f'/fixture/released/{wake_a.id}')
                response.raise_for_status()
                result_a = response.json()
                assert result_a['view']['release_complete'] and result_a['view']['exit_kind'] == 'completed'
                assert result_a['view']['report_cursor'] == 2
                assert provider.stages[-1] == 6 and not provider.cancelled
                before_wait = [value for stage, value in zip(provider.stages, provider.inputs) if stage <= 3]
                assert NOTICE in json.dumps(next(value for stage, value in zip(provider.stages, provider.inputs)
                                                if stage == 3), ensure_ascii=False)
                assert A2 not in json.dumps(before_wait)
                assert A2 in json.dumps(next(value for stage, value in zip(provider.stages, provider.inputs)
                                            if stage == 4))
                assert B not in json.dumps(provider.inputs)
                assert (await repo.get_item(a1.id)).status == 'processed'
                assert (await repo.get_item(a2.id)).status == 'processed'
                assert (await repo.get_item(a2.id)).exposed_wake_id == wake_a.id
                assert len(result_a['notices']) == 1
                assert not any(value['type'] in {'task.steered', 'task.cancelled'} for value in result_a['updates'])
                assert not result_a['view']['cancel_requested']

                await scheduler.process_chat(a1.chat_id)
                wake_b = await repo.get_wake(saved_b.wake_id)
                response = await http.get(gateway_url + f'/fixture/released/{wake_b.id}')
                response.raise_for_status()
                result_b = response.json()
                assert result_b['view']['release_complete'] and result_b['view']['exit_kind'] == 'completed'
                assert result_b['view']['session_id'] != binding_a['session_id']
                assert result_b['view']['report_cursor'] == 1
                assert (await repo.get_item(b.id)).status == 'processed'
                b_inputs = [value for stage, value in zip(provider.stages, provider.inputs) if 7 <= stage <= 9]
                assert B in json.dumps(b_inputs)
                assert A2 not in json.dumps(b_inputs)

                # All domain clocks advance together; no five-minute wall-clock sleep.
                now[0] += timedelta(seconds=301)
                response = await http.post(gateway_url + '/fixture/clock', json={'now': now[0].isoformat()})
                response.raise_for_status()
                candidates = await client.sessions(SCOPE)
                assert {value['session_id'] for value in candidates['sessions']} == {
                    binding_a['session_id'], result_b['view']['session_id']}
                a3 = await observe(A3, '1004')
                await scheduler.process_chat(a1.chat_id)
                wake_a3 = await repo.get_wake((await repo.get_item(a3.id)).wake_id)
                assert str(wake_a3.payload.target_session_id) == binding_a['session_id']
                response = await http.get(gateway_url + f'/fixture/released/{wake_a3.id}')
                response.raise_for_status()
                result_a3 = response.json()
                assert result_a3['view']['release_complete'] and result_a3['view']['exit_kind'] == 'completed'
                assert result_a3['view']['session_id'] == binding_a['session_id']
                assert result_a3['view']['task_id'] != binding_a['task_id']
                assert result_a3['binding']['binding_id'] != binding_a['binding_id']
                assert result_a3['view']['report_cursor'] == 1
                assert (await repo.get_item(a3.id)).status == 'processed'
                a3_inputs = [value for stage, value in zip(provider.stages, provider.inputs) if stage >= 10]
                assert provider.stages[-1] == 12 and set(provider.stages) == set(range(1, 13))
                assert A3 in json.dumps(a3_inputs)
                assert B not in json.dumps(a3_inputs)
                assert provider.failures == []
                if delay_delivery:
                    assert provider.retries.get(5) == 1
                assert all(
                    not any(secret in json.dumps(value) for secret in (
                        str(a1.id), str(a2.id), str(b.id), str(a3.id), str(wake_a.id),
                        binding_a['binding_id'], result_a3['binding']['binding_id']))
                    for value in provider.inputs)
                assert any(row['type'] == 'argus_report_synced' and
                           row['payload']['binding_id'] == result_a3['binding']['binding_id']
                           for row in result_a3['records'])
                assert len(routes) == 2  # Interleaved A2/B, then related A3.
        finally:
            (tmp_path / 'provider-inputs.json').write_text(json.dumps(provider.inputs, ensure_ascii=False, indent=2))
            (tmp_path / 'provider-outcomes.json').write_text(json.dumps(provider.outcomes, ensure_ascii=False, indent=2))
            provider.initial_release.set()
            provider.release.set()
            provider.pending_report_seen.set()
            if scheduler is not None:
                await scheduler.aclose()
            if client is not None:
                await client.aclose()
            if process is not None and process.returncode is None:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), 10)
                except TimeoutError:
                    process.kill()
                    await asyncio.wait_for(process.wait(), 5)
