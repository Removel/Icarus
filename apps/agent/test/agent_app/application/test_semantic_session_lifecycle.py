"""Semantic idle is soft; expiry requires durable local and remote quiescence."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from pydantic import ValidationError

from agent_app.application.agent_runtime import AgentRuntime
from agent_app.application.repositories.session_store import SessionStore
from agent_app.argus_protocol import HandoffError
from agent_app.config.config_model import ArgusSettings
from icarus.core.identity import SessionIdentity
from icarus.core.runtime_update import RuntimeUpdate
from icarus.core.session import SessionRuntimeSnapshot
from icarus.core.tools.execution_tracker import ToolExecutionTracker
from apps.agent.test.agent_app.application.test_scoped_execution import request_v2, WAKE, SESSION, EXECUTION, TASK
from apps.agent.test.agent_app.application.test_session_runtime import make_config


class Clock:
    def __init__(self): self.now = datetime(2026, 10, 25, tzinfo=UTC)
    def __call__(self): return self.now
    def advance(self, seconds): self.now += timedelta(seconds=seconds)


class Client:
    def __init__(self): self.pending, self.last_related, self.unavailable = 0, None, False
    async def session_pending(self, session_id, scope):
        if self.unavailable: raise HandoffError('unavailable', retryable=True)
        return {'pending_count': self.pending, 'last_related_at': self.last_related}
    async def aclose(self): pass


async def setup_idle(tmp_path, *, client=None):
    clock, client = Clock(), client or Client()
    store = SessionStore(tmp_path/'data', clock=clock)
    await store.start()
    identity = SessionIdentity.create(tmp_path/'workspace', SESSION)
    req = request_v2()
    await store.reserve_scoped(req, identity=identity, execution_id=EXECUTION, task_id=TASK)
    await store.update_scoped(WAKE, {'binding': {'binding_id': 'binding'}, 'execution_stopped': True,
                                    'phase': 'stopped', 'exit_kind': 'completed', 'sync_state': 'finalized'})
    await store.complete_scoped_release(WAKE)
    config = make_config(tmp_path/'data')
    runtime = AgentRuntime(session_store=store, config_loader=lambda: config, clock=clock, handoff_client=client)
    await runtime.start()
    return runtime, store, identity, clock, client


def test_semantic_config_defaults_and_strict_safe_retention():
    settings = ArgusSettings()
    assert settings.session_idle_seconds == 300 and settings.session_retention_seconds == 86400
    for values in ({'session_idle_seconds': 0}, {'session_retention_seconds': float('inf')},
                   {'session_idle_seconds': 300, 'session_retention_seconds': 300}):
        with pytest.raises(ValidationError): ArgusSettings(**values)


def test_soft_idle_unloads_at_300_resumes_at_301_and_safe_retirement_keeps_history(tmp_path):
    async def run():
        runtime, store, identity, clock, client = await setup_idle(tmp_path)
        stopped = []
        class Session:
            tool_execution_tracker = ToolExecutionTracker()
            def snapshot(self): return SessionRuntimeSnapshot((), 0, 0, 0, 0, None, None)
            async def stop(self, reason): stopped.append(reason)
        entry = await runtime._entry(identity)
        entry.runtime, entry.lifecycle = Session(), 'ready'
        await store.append_update(identity, RuntimeUpdate(workspace_key=identity.workspace_key,
            session_id=identity.session_id, task_id=TASK, type='user.message', payload={'text': 'navigation'}, occurred_at=clock()))
        try:
            clock.advance(299)
            await runtime.cleanup_argus_sessions()
            assert not stopped
            clock.advance(1)
            await runtime.cleanup_argus_sessions()
            assert stopped == ['argus_soft_idle'] and entry.runtime is None
            assert (await runtime.scoped_sessions(request_v2().scope))['items'][0]['session_id'] == SESSION
            clock.advance(1)
            followup = request_v2(wake_id=str(UUID(int=11001)), session_id=SESSION)
            await store.reserve_scoped(followup, identity=identity, execution_id=str(UUID(int=11002)), task_id=str(UUID(int=11003)))
            await store.update_scoped(followup.wake_id, {'execution_stopped': True, 'phase': 'stopped',
                'exit_kind': 'completed', 'sync_state': 'finalized', 'binding': {'binding_id': 'next'}})
            await store.complete_scoped_release(followup.wake_id)
            clock.advance(86399)
            await runtime.cleanup_argus_sessions()
            assert (await store.list_argus_sessions(followup.scope))['items']
            clock.advance(1)
            await runtime.cleanup_argus_sessions()
            assert not (await store.list_argus_sessions(followup.scope))['items']
            record = await store.get_session(identity)
            assert record.deleted_at is None and record.properties['retired_at'] == clock().isoformat()
            updates, _ = await store.read_updates(identity)
            assert any(row.payload.get('text') == 'navigation' for row in updates)
        finally: await runtime.stop()
    asyncio.run(run())


@pytest.mark.parametrize('protection', ['pending', 'unavailable', 'future', 'naive', 'tracker', 'outbox', 'active'])
def test_remote_waiting_unavailable_and_actual_work_protect_past_24_hours(tmp_path, protection):
    async def run():
        runtime, store, identity, clock, client = await setup_idle(tmp_path)
        try:
            clock.advance(86401)
            if protection == 'pending': client.pending = 1
            if protection == 'unavailable': client.unavailable = True
            if protection == 'future': client.last_related = (clock() + timedelta(days=1)).isoformat()
            if protection == 'naive': client.last_related = '2026-10-25T00:00:00'
            if protection == 'outbox':
                await store.append_internal(identity, task_id=TASK, update_type='argus_report', payload={
                    'report_id': 'unknown', 'items': [], 'fingerprint': 'unknown'})
            if protection == 'active':
                await store.reserve_scoped(request_v2(wake_id=str(UUID(int=11004)), session_id=SESSION),
                    identity=identity, execution_id=str(UUID(int=11005)), task_id=str(UUID(int=11006)))
            if protection == 'tracker':
                entry = await runtime._entry(identity)
                tracker = ToolExecutionTracker()
                future = asyncio.get_running_loop().create_future()
                tracker.track(future)
                class Session:
                    tool_execution_tracker = tracker
                    def snapshot(self): return SessionRuntimeSnapshot((), 0, 0, 0, 0, None, None)
                    async def stop(self, reason): raise AssertionError('Must not unload actual tool work')
                entry.runtime, entry.lifecycle = Session(), 'ready'
            await runtime.cleanup_argus_sessions()
            assert (await store.list_argus_sessions(request_v2().scope))['items']
            assert (await store.get_session(identity)).properties.get('retired_at') is None
        finally:
            if protection == 'tracker':
                future.set_result(None)
                entry.runtime = None
            if protection == 'active':
                await store.update_scoped(str(UUID(int=11004)), {'execution_stopped': True, 'phase': 'stopped',
                    'exit_kind': 'completed', 'sync_state': 'finalized'})
                await store.complete_scoped_release(str(UUID(int=11004)))
            await runtime.stop()
    asyncio.run(run())


def test_sync_retry_and_status_do_not_renew_semantic_activity(tmp_path):
    async def run():
        runtime, store, identity, clock, client = await setup_idle(tmp_path)
        try:
            before = (await store.get_session(identity)).properties['last_semantic_activity_at']
            clock.advance(86400)
            await store.update_scoped(WAKE, {'sync_state': 'finalized', 'report_cursor': 1})
            await store.get_scoped(wake_id=WAKE)
            await store.complete_scoped_release(WAKE)
            assert (await store.get_session(identity)).properties['last_semantic_activity_at'] == before
            clock.advance(-86401)
            await runtime.cleanup_argus_sessions()
            assert (await store.list_argus_sessions(request_v2().scope))['items']
        finally: await runtime.stop()
    asyncio.run(run())


def test_waiting_a2_preserves_a_while_b_owns_chat_across_a_deadline(tmp_path):
    async def run():
        runtime, store, identity, clock, client = await setup_idle(tmp_path)
        other = SessionIdentity.create(tmp_path/'workspace', str(UUID(int=12001)))
        other_request = request_v2(wake_id=str(UUID(int=12002)))
        try:
            clock.advance(86399)
            await store.reserve_scoped(other_request, identity=other,
                execution_id=str(UUID(int=12003)), task_id=str(UUID(int=12004)))
            async def pending(session_id, scope):
                return {'pending_count': 1 if session_id == SESSION else 0, 'last_related_at': None}
            client.session_pending = pending
            clock.advance(2)
            await runtime.cleanup_argus_sessions()
            candidates = await runtime.scoped_sessions(request_v2().scope)
            assert {value['session_id'] for value in candidates['items']} == {SESSION, other.session_id}
            assert next(value for value in candidates['items'] if value['session_id'] == SESSION)['has_work']
            assert (await store.get_scoped(scope=request_v2().scope)).session_id == other.session_id
            client.session_pending = Client().session_pending
            await runtime.cleanup_argus_sessions()
            assert (await store.get_session(identity)).properties.get('retired_at') is not None
            assert (await store.get_session(other)).properties.get('retired_at') is None
        finally: await runtime.stop()
    asyncio.run(run())


def test_stale_zero_retirement_recovers_exact_assigned_continuation_after_b_releases(tmp_path):
    from agent_app.application.controllers.scoped_execution import ScopedExecutionController
    from agent_app.application.models.scoped_execution import ScopedExecutionError
    async def run():
        runtime, store, identity, clock, client = await setup_idle(tmp_path)
        zero_taken, release_zero, allow_exit = asyncio.Event(), asyncio.Event(), asyncio.Event()
        assigned = {}
        requests, launches = [], []
        other = SessionIdentity.create(tmp_path/'workspace', str(UUID(int=13001)))
        b = request_v2(wake_id=str(UUID(int=13002)))
        a2 = request_v2(wake_id=str(UUID(int=13003)), session_id=SESSION)
        original_pending = client.session_pending
        async def pending(session_id, scope):
            if session_id == SESSION and not zero_taken.is_set():
                stale = {'pending_count': 0, 'last_related_at': None}
                zero_taken.set()
                await release_zero.wait()
                return stale
            return {'pending_count': len(assigned) if session_id == SESSION else 0,
                    'last_related_at': clock().isoformat() if assigned else None}
        client.session_pending = pending
        async def bind(request, view):
            requests.append((request, view))
            if assigned.get(request.wake_id) != request.fingerprint:
                raise HandoffError('conflict')
            return {'protocol_version': 2, 'binding_id': 'a2-binding', 'session_id': view.session_id,
                    'task_id': view.task_id, 'execution_id': view.execution_id,
                    'scope': request.scope.model_dump(mode='json'), 'origin_boot_id': request.origin_boot_id,
                    'request_fingerprint': request.fingerprint,
                    'initial_items': [dict(value.model_dump(mode='json'), role='work') for value in request.items]}
        client.bind = bind
        async def finalize(*args): return {'finalized': True, 'report_cursor': 0}
        client.finalize = finalize
        class Session:
            async def drain_execution(self): await allow_exit.wait()
            async def cancel_task(self, *args): allow_exit.set()
        async def submit(identity, task_id, prompt, **kwargs): launches.append((identity, task_id)); return Session()
        controller = ScopedExecutionController(store, client, workspace=identity.workspace_path,
            submit_reserved=submit, flush_updates=lambda: asyncio.sleep(0))
        try:
            await store.append_update(identity, RuntimeUpdate(workspace_key=identity.workspace_key,
                session_id=SESSION, task_id=TASK, type='user.message', payload={'text': 'A retained topic'}, occurred_at=clock()))
            clock.advance(86401)
            await store.reserve_scoped(b, identity=other, execution_id=str(UUID(int=13004)), task_id=str(UUID(int=13005)))
            cleanup = asyncio.create_task(runtime.cleanup_argus_sessions())
            await asyncio.wait_for(zero_taken.wait(), 1)
            assigned[a2.wake_id] = a2.fingerprint  # Remote acceptance occurs after the zero snapshot.
            release_zero.set()
            await asyncio.wait_for(cleanup, 1)
            assert (await store.get_session(identity)).properties['retired_at'] is not None
            await store.update_scoped(b.wake_id, {'binding': {'binding_id': 'b-binding'}, 'execution_stopped': True,
                'phase': 'stopped', 'exit_kind': 'completed', 'sync_state': 'finalized'})
            await store.complete_scoped_release(b.wake_id)
            view = await controller.submit(a2)
            assert view.session_id == SESSION and launches == [(identity, view.task_id)]
            assert (await store.get_session(identity)).properties.get('retired_at') is None
            assert (await store.load_binding(identity, view.task_id))['binding_id'] == 'a2-binding'
            history, _ = await store.read_updates(identity)
            assert any(row.payload.get('text') == 'A retained topic' for row in history)
        finally:
            release_zero.set(); allow_exit.set()
            await controller.close(); await runtime.stop()
    asyncio.run(run())


@pytest.mark.parametrize('remote', ['empty', 'unrelated', 'unavailable', 'wrong-proof'])
def test_retired_target_without_exact_authenticated_assignment_is_not_revived(tmp_path, remote):
    from agent_app.application.controllers.scoped_execution import ScopedExecutionController
    from agent_app.application.models.scoped_execution import ScopedExecutionError
    async def run():
        runtime, store, identity, clock, client = await setup_idle(tmp_path)
        try:
            clock.advance(86401)
            await runtime.cleanup_argus_sessions()
            retired = (await store.get_session(identity)).properties['retired_at']
            async def pending(session_id, scope):
                if remote == 'unavailable': raise HandoffError('unavailable')
                return {'pending_count': 0 if remote == 'empty' else 1, 'last_related_at': clock().isoformat()}
            client.session_pending = pending
            async def bind(request, view):
                if remote == 'unrelated': raise HandoffError('conflict')
                return {'binding_id': 'not-proof', 'session_id': 'another-session'}
            client.bind = bind
            async def submit(*args, **kwargs): raise AssertionError('Unassigned retired target must not execute')
            controller = ScopedExecutionController(store, client, workspace=identity.workspace_path,
                submit_reserved=submit, flush_updates=lambda: asyncio.sleep(0))
            try:
                value = request_v2(wake_id=str(UUID(int=13010)), session_id=SESSION)
                with pytest.raises((ScopedExecutionError, HandoffError)):
                    await controller.submit(value)
                assert (await store.get_session(identity)).properties['retired_at'] == retired
                admission = await store.scoped_record(value.wake_id)
                if remote in {'empty', 'unavailable'}:
                    assert admission is None
                elif remote == 'unrelated':
                    assert admission.properties['release_complete'] and admission.properties['exit_kind'] == 'start_failed'
                else:
                    assert admission is not None and not admission.properties.get('launching')
                if remote != 'wrong-proof':
                    assert await store.get_scoped(scope=value.scope) is None
            finally: await controller.close()
        finally: await runtime.stop()
    asyncio.run(run())


def test_provisional_retired_admission_survives_unavailable_bind_and_recovers_terminal_rejection(tmp_path):
    from agent_app.application.controllers.scoped_execution import ScopedExecutionController
    async def run():
        runtime, store, identity, clock, client = await setup_idle(tmp_path)
        bound_ids = []
        try:
            clock.advance(86401)
            await runtime.cleanup_argus_sessions()
            before = (await store.get_session(identity)).properties
            async def pending(session_id, scope):
                return {'pending_count': 1, 'last_related_at': clock().isoformat()}
            client.session_pending = pending
            reject = False
            async def bind(value, view):
                durable = await store.scoped_record(value.wake_id)
                assert durable is not None and durable.properties['task_id'] == view.task_id
                bound_ids.append((view.execution_id, view.task_id))
                raise HandoffError('conflict' if reject else 'unavailable', retryable=not reject)
            client.bind = bind
            async def submit(*args, **kwargs): raise AssertionError('Provisional recovery must not launch model')
            value = request_v2(wake_id=str(UUID(int=13011)), session_id=SESSION)
            first = ScopedExecutionController(store, client, workspace=identity.workspace_path,
                submit_reserved=submit, flush_updates=lambda: asyncio.sleep(0))
            with pytest.raises(HandoffError): await first._submit(value)
            provisional = await store.scoped_record(value.wake_id)
            assert provisional.properties.get('reactivating_retired_at') == before['retired_at']
            assert (await store.get_session(identity)).properties['last_semantic_activity_at'] == before['last_semantic_activity_at']
            reject = True
            restored = ScopedExecutionController(store, client, workspace=identity.workspace_path,
                submit_reserved=submit, flush_updates=lambda: asyncio.sleep(0))
            await restored.recover()
            assert len(bound_ids) == 2 and bound_ids[0] == bound_ids[1]
            terminal = await store.get_scoped(wake_id=value.wake_id)
            assert terminal.release_complete and terminal.exit_kind == 'start_failed'
            assert await store.get_scoped(scope=value.scope) is None
            assert (await store.get_session(identity)).properties['retired_at'] == before['retired_at']
        finally: await runtime.stop()
    asyncio.run(run())


@pytest.mark.parametrize('recover_first', [False, True])
def test_oversized_successful_bind_stays_owned_and_retries_same_durable_ids(tmp_path, recover_first):
    import json
    import httpx
    from agent_app.application.controllers.scoped_execution import ScopedExecutionController
    from agent_app.application.services.argus_handoff_client import ArgusHandoffClient
    async def run():
        runtime, store, identity, clock, _ = await setup_idle(tmp_path)
        committed, launches, responses = [], [], {'oversized': True}
        value = request_v2(wake_id=str(UUID(int=13012)), session_id=SESSION)
        async def handler(request):
            body = json.loads(request.content)
            if request.url.path.endswith('/pending'):
                return httpx.Response(200, json={'protocol_version': 2, 'pending_count': 1,
                                                 'last_related_at': clock().isoformat()})
            if request.url.path.endswith('/bind'):
                durable = await store.scoped_record(value.wake_id)
                assert durable is not None and durable.properties['task_id'] == body['task_id']
                committed.append(body)
                if responses['oversized']:
                    return httpx.Response(200, content=b'x' * 1048577)
                return httpx.Response(200, json={**body, 'binding_id': 'committed-binding',
                    'initial_items': [dict(item.model_dump(mode='json'), role='work') for item in value.items]})
            if request.url.path.endswith('/finalize'):
                return httpx.Response(200, json={'protocol_version': 2, 'finalized': True, 'report_cursor': 0})
            raise AssertionError(request.url)
        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        client = ArgusHandoffClient('http://argus.fixture', 'test-token', http=http)
        class Session:
            async def drain_execution(self): pass
        async def submit(*args, **kwargs): launches.append(args); return Session()
        controller = ScopedExecutionController(store, client, workspace=identity.workspace_path,
            submit_reserved=submit, flush_updates=lambda: asyncio.sleep(0))
        try:
            clock.advance(86401)
            await runtime.cleanup_argus_sessions()
            if recover_first:
                # Establish an uncertain persisted bind before invoking recovery;
                # recovery's own HTTP200 oversized body must not release ownership.
                async def unavailable(request, view): raise HandoffError('unavailable', retryable=True)
                original_bind = client.bind
                client.bind = unavailable
                with pytest.raises(HandoffError): await controller._submit(value)
                client.bind = original_bind
                await controller.recover()
            else:
                with pytest.raises(HandoffError, match='response_too_large'):
                    await controller.submit(value)
            uncertain = await store.get_scoped(wake_id=value.wake_id)
            assert not uncertain.release_complete
            assert (await store.get_scoped(scope=value.scope)).execution_id == uncertain.execution_id
            assert (await store.get_session(identity)).properties.get('retired_at') is not None
            assert launches == []
            responses['oversized'] = False
            if recover_first:
                await controller.recover()
                assert (await store.get_scoped(wake_id=value.wake_id)).release_complete
                assert launches == []
            else:
                resumed = await controller.submit(value)
                await controller.drain()
                assert resumed.execution_id == uncertain.execution_id and len(launches) == 1
            assert len(committed) == 2
            assert committed[0]['task_id'] == committed[1]['task_id'] == uncertain.task_id
            assert committed[0]['execution_id'] == committed[1]['execution_id'] == uncertain.execution_id
        finally:
            await controller.close(); await http.aclose(); await runtime.stop()
    asyncio.run(run())


def test_semantic_task_prompt_and_candidate_summary_include_actual_subject(tmp_path):
    from agent_app.application.controllers.scoped_execution import ScopedExecutionController
    async def run():
        store = SessionStore(tmp_path/'data')
        await store.start()
        prompts = []
        class BoundClient:
            async def bind(self, request, view):
                return {'binding_id': 'binding', 'initial_items': [{'observation_id': request.items[0].observation_id,
                    'role': 'work', 'sender_name': 'Ada', 'excerpt': 'Lunar navigation loses the north star',
                    'sent_at': '2026-10-25T00:00:00Z', 'locator': request.items[0].locator.model_dump()}]}
            async def finalize(self, *args): return {}
        class Session:
            async def drain_execution(self): pass
        async def submit(identity, task_id, prompt, **kwargs): prompts.append(prompt); return Session()
        controller = ScopedExecutionController(store, BoundClient(), workspace=tmp_path/'workspace',
            submit_reserved=submit, flush_updates=lambda: asyncio.sleep(0))
        try:
            view = await controller.submit(request_v2())
            await controller.drain()
            assert 'Lunar navigation' in prompts[0] and 'Ada' in prompts[0]
            assert 'observation_id' not in prompts[0] and view.wake_id not in prompts[0]
            candidate = (await store.list_argus_sessions(view.scope))['items'][0]
            assert 'Lunar navigation' in candidate['summary']
        finally: await controller.close(); await store.close()
    asyncio.run(run())
