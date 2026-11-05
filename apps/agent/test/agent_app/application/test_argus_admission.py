"""Admission evidence, not missing logs, controls safe delivery retry."""
import asyncio
import importlib.util
from types import SimpleNamespace
from uuid import uuid4

import pytest

from agent_app.application import AgentRuntime
from agent_app.application import errors
from agent_app.application.repositories.session_store import SessionStore
from agent_app.application.services.argus_bridge import ArgusBridge, BridgeConflict
from agent_app.argus_protocol import WakeRequest
from icarus.core.contracts.input import InputAccepted
from icarus.core.identity import SessionIdentity
from .test_agent_runtime import RuntimeFactory, make_config


class Runtime:
    def __init__(self, store):
        self.session_store = store
        self.calls = 0
        self.error = None

    def configure_bridge_session(self, *args):
        pass

    async def create_session(self, workspace, session_id, **kwargs):
        await self.session_store.create_session(SessionIdentity.create(workspace, session_id))

    async def submit(self, *args, **kwargs):
        self.calls += 1
        if self.error:
            raise self.error
        return InputAccepted(kwargs['task_id'], 0)


async def setup_bridge(tmp_path):
    store = SessionStore(tmp_path / 'data')
    await store.start()
    runtime = Runtime(store)
    bridge = ArgusBridge(runtime, workspace_path=tmp_path / 'work')
    channel, wake, binding, item = [str(uuid4()) for _ in range(4)]
    session = await bridge.ensure_session(channel, 'select', 'slot')
    request = WakeRequest(channel_id=channel, wake_id=wake, binding_id=binding,
        channel_version=1, session_id=session['session_id'], session_key='slot',
        channel_snapshot=dict(id=channel, title='task', goal='goal', progress='',
            lifecycle='active', version=1, source_keys=['qq:1:group:2'], rule_source=''),
        rule_sdk={'version': 2}, items=[dict(id=item, source_key='qq:1:group:2',
            message_id='1', kind='message.observed', revision=1,
            observed_at='2026-11-03T12:30:00Z', content={'text': 'synthetic request'}, detail={})],
        argus_url='http://127.0.0.1:1')
    return store, runtime, bridge, request


def test_pre_enqueue_load_failure_is_retryable(tmp_path):
    async def run():
        factory = RuntimeFactory(error=RuntimeError('synthetic load failure'))
        runtime = AgentRuntime(config_loader=lambda: make_config(tmp_path / 'data'), session_factory=factory)
        await runtime.start()
        try:
            bridge = ArgusBridge(runtime, workspace_path=tmp_path / 'work')
            channel = str(uuid4())
            session = await bridge.ensure_session(channel, 'select', 'slot')
            with pytest.raises(Exception) as caught:
                await runtime.submit(tmp_path / 'work', session['session_id'], 'hello',
                    submission_id='one', task_id='task', bridge=True)
            assert type(caught.value).__name__ == 'SubmissionNotAdmitted'
            assert caught.value.retryable is True
            assert factory.created[0].submit_count == 0
            factory.error = None
            accepted = await runtime.submit(tmp_path / 'work', session['session_id'], 'hello',
                submission_id='one', task_id='task', bridge=True)
            assert accepted.task_id == 'task'
        finally:
            await runtime.stop()
    asyncio.run(run())


def test_safe_failure_retries_same_wake_after_restart(tmp_path):
    async def run():
        assert hasattr(errors, 'SubmissionNotAdmitted'), 'missing explicit not-admitted failure'
        store, runtime, bridge, request = await setup_bridge(tmp_path)
        try:
            runtime.error = errors.SubmissionNotAdmitted('runtime_load_failed', retryable=True)
            first = await bridge.wake(request)
            assert first['admission_state'] == 'not_admitted'
            assert first['retryable'] is True
            runtime.error = None
            restarted = ArgusBridge(runtime, workspace_path=bridge.workspace)
            result = await restarted.wake(request)
            assert result['admission_state'] == 'accepted'
            assert result['attempt'] == 2
            assert result['task_id'] == first['task_id']
            assert runtime.calls == 2
            assert len(await store.list_session_ids(bridge.identity_for_wake(request.model_dump()).workspace_key)) == 1
            assert (await restarted.wake(request))['task_id'] == result['task_id']
            assert runtime.calls == 2
        finally:
            await store.close()
    asyncio.run(run())


def test_enqueue_then_raise_is_unknown(tmp_path):
    async def run():
        store, runtime, bridge, request = await setup_bridge(tmp_path)
        try:
            runtime.error = RuntimeError('may have enqueued')
            with pytest.raises(RuntimeError):
                await bridge.wake(request)
            result = await bridge.wake(request)
            assert result.get('admission_state') == 'unknown'
            assert result['retryable'] is False
            assert runtime.calls == 1
            other = request.model_copy(update={'wake_id': str(uuid4()), 'binding_id': str(uuid4())})
            with pytest.raises(BridgeConflict, match='unresolved'):
                await bridge.wake(other)
        finally:
            await store.close()
    asyncio.run(run())


def test_admitted_then_journal_failure_is_unknown(tmp_path):
    async def run():
        store, runtime, bridge, request = await setup_bridge(tmp_path)
        original = bridge.append
        async def fail_accept(identity, kind, payload, task_id=None):
            if kind == 'argus.submission' and payload['phase'] == 'accepted':
                raise OSError('synthetic journal failure')
            return await original(identity, kind, payload, task_id)
        bridge.append = fail_accept
        try:
            with pytest.raises(OSError):
                await bridge.wake(request)
            restarted = ArgusBridge(runtime, workspace_path=bridge.workspace)
            result = await restarted.wake(request)
            assert result['admission_state'] == 'unknown'
            assert not result['retryable']
            assert runtime.calls == 1
        finally:
            await store.close()
    asyncio.run(run())


def test_safe_resume_rejects_corrupt_durable_aliases(tmp_path):
    async def run():
        store,runtime,bridge,request=await setup_bridge(tmp_path)
        try:
            runtime.error=errors.SubmissionNotAdmitted('runtime_load_failed',retryable=True)
            await bridge.wake(request)
            original=store.read_updates
            async def missing_alias(identity,**kwargs):
                rows,cursor=await original(identity,**kwargs)
                return tuple(r for r in rows if r.type!='argus.references'),cursor
            store.read_updates=missing_alias
            runtime.error=None
            restarted=ArgusBridge(runtime,workspace_path=bridge.workspace)
            with pytest.raises(ValueError,match='reference_state_corrupt'):
                await restarted.wake(request)
            assert runtime.calls==1
        finally:await store.close()
    asyncio.run(run())


def test_prepared_journal_failure_never_submits(tmp_path):
    async def run():
        store, runtime, bridge, request = await setup_bridge(tmp_path)
        original = bridge.append
        async def fail_prepare(identity, kind, payload, task_id=None):
            if kind == 'argus.submission':
                raise OSError('synthetic journal unavailable')
            return await original(identity, kind, payload, task_id)
        bridge.append = fail_prepare
        try:
            with pytest.raises(OSError):
                await bridge.wake(request)
            assert runtime.calls == 0
        finally:
            await store.close()
    asyncio.run(run())


def test_cancelled_submission_is_not_replayed(tmp_path):
    async def run():
        store, runtime, bridge, request = await setup_bridge(tmp_path)
        try:
            runtime.error = asyncio.CancelledError()
            with pytest.raises(asyncio.CancelledError):
                await bridge.wake(request)
            restarted = ArgusBridge(runtime, workspace_path=bridge.workspace)
            result = await restarted.wake(request)
            assert result['admission_state'] == 'unknown' and not result['retryable']
            assert runtime.calls == 1
        finally:
            await store.close()
    asyncio.run(run())


def test_same_wake_concurrent_retry_only_submits_once(tmp_path):
    async def run():
        store, runtime, bridge, request = await setup_bridge(tmp_path)
        try:
            a, b = await asyncio.gather(bridge.wake(request), bridge.wake(request))
            assert a['task_id'] == b['task_id'] and runtime.calls == 1
        finally:
            await store.close()
    asyncio.run(run())


def test_independent_instances_observe_same_region_without_sharing_progress(tmp_path):
    async def run():
        a = await setup_bridge(tmp_path/'a')
        b = await setup_bridge(tmp_path/'b')
        try:
            # The same external region is observed through legitimate independent selections.
            await a[2].wake(a[3])
            records_b, _ = await b[0].read_updates(b[2].identity_for_wake(b[3].model_dump()))
            assert not any(r.type == 'argus.wake' for r in records_b)
            await b[2].wake(b[3])
            assert a[1].calls == b[1].calls == 1
            assert a[3].items[0].source_key == b[3].items[0].source_key
        finally:
            await a[0].close(); await b[0].close()
    asyncio.run(run())

@pytest.mark.parametrize('later_phase', ['submitting', 'accepted', 'unknown'])
def test_stale_attempt_cannot_reopen_retry(later_phase):
    assert importlib.util.find_spec('agent_app.application.services.argus_delivery'), 'missing admission reducer'
    from agent_app.application.services.argus_delivery import submission_state
    def event(attempt, phase):
        return SimpleNamespace(type='argus.submission', task_id='task', payload={
            'attempt': attempt, 'phase': phase, 'retryable': phase == 'not_admitted'})
    state = submission_state((event(1, 'not_admitted'), event(2, 'submitting'),
        event(2, later_phase), event(1, 'not_admitted')), task_id='task')
    assert state.attempt == 2
    assert state.phase == ('unknown' if later_phase == 'submitting' else later_phase)
    assert not state.retryable


def test_unsafe_attempt_cannot_be_erased_by_later_rejection():
    assert importlib.util.find_spec('agent_app.application.services.argus_delivery'), 'missing admission reducer'
    from agent_app.application.services.argus_delivery import submission_state
    records = tuple(SimpleNamespace(type='argus.submission', task_id='task', payload={
        'attempt': n, 'phase': phase, 'retryable': True})
        for n, phase in [(1, 'unknown'), (2, 'not_admitted')])
    state = submission_state(records, task_id='task')
    assert state.phase == 'unknown'
    assert not state.retryable
