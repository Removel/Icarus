"""Whole-change review regressions: transient binding, durable terminal proof and cleanup."""

import asyncio

import pytest

from agent_app.application.controllers.scoped_execution import ScopedExecutionController
from agent_app.application.repositories.session_store import SessionStore
from agent_app.argus_protocol import HandoffError
from apps.agent.test.agent_app.application.test_scoped_execution import request, WAKE



def test_transient_bind_failure_is_retried_with_same_identity_without_second_wake(tmp_path):
    async def run():
        store = SessionStore(tmp_path/'data')
        await store.start()
        identities, launches = [], []
        class Client:
            async def bind(self, value, view):
                identities.append(view.execution_id)
                if len(identities) == 1: raise HandoffError('unavailable', retryable=True)
                return {'binding_id':'binding'}
            async def finalize(self, *args): return {'finalized':True,'report_cursor':0}
        class Session:
            async def drain_execution(self): pass
        async def submit(*args, **kwargs): launches.append(1); return Session()
        async def flush(): pass
        controller = ScopedExecutionController(store, Client(), workspace=tmp_path/'workspace',
            submit_reserved=submit, flush_updates=flush, retry_seconds=0.01)
        try:
            try:
                await controller.submit(request())
            except HandoffError:
                pass
            async with asyncio.timeout(1):
                while not (await controller.status(wake_id=WAKE)).release_complete:
                    await asyncio.sleep(0.005)
            assert len(identities) == 2 and identities[0] == identities[1]
            assert launches == [1]
        finally:
            await controller.close()
            await store.close()
    asyncio.run(run())


def test_cancel_during_session_load_is_delivered_when_session_registers(tmp_path):
    async def run():
        store = SessionStore(tmp_path/'data')
        await store.start()
        loading, loaded, exited = asyncio.Event(), asyncio.Event(), asyncio.Event()
        cancellations = []
        class Client:
            async def bind(self, request, view): return {'binding_id':'binding'}
            async def finalize(self, *args): return {'finalized':True,'report_cursor':0}
        class Session:
            async def drain_execution(self): await exited.wait()
            async def cancel_task(self, task_id, reason): cancellations.append(task_id); exited.set()
        async def submit(*args, **kwargs):
            loading.set()
            await loaded.wait()
            return Session()
        async def flush(): pass
        controller = ScopedExecutionController(store, Client(), workspace=tmp_path/'workspace',
            submit_reserved=submit, flush_updates=flush)
        try:
            pending = asyncio.create_task(controller.submit(request()))
            await loading.wait()
            view = await controller.cancel(WAKE, 'cancel-during-load')
            assert view.cancel_requested and not view.execution_stopped
            loaded.set()
            await pending
            await asyncio.sleep(0)
            assert cancellations == [view.task_id]
            await asyncio.wait_for(controller.drain(), 1)
        finally:
            loaded.set()
            exited.set()
            await controller.close()
            await store.close()
    asyncio.run(run())


def test_release_retains_semantic_runtime_and_shutdown_still_cleans_up(tmp_path):
    from agent_app.application.agent_runtime import AgentRuntime
    from apps.agent.test.agent_app.application.test_scoped_execution import request_v2
    from apps.agent.test.agent_app.application.test_session_runtime import make_config
    from icarus.core.contracts.input import InputAccepted
    from icarus.core.runtime_update import RuntimeUpdate
    from icarus.core.session import SessionRuntimeSnapshot
    from datetime import UTC, datetime
    from uuid import UUID

    async def run():
        created, stopped, submitted = [], [], []
        class Session:
            def __init__(self, identity, *, publish_update, **kwargs):
                self.identity, self.publish_update, self.task_id = identity, publish_update, None
                created.append(self)
            async def start(self): pass
            async def submit(self, prompt, *, task_id):
                self.task_id = task_id
                submitted.append(task_id)
                return InputAccepted(task_id, 0)
            async def drain_execution(self):
                await self.publish_update(RuntimeUpdate(workspace_key=self.identity.workspace_key,
                    session_id=self.identity.session_id, task_id=self.task_id, type='task.finished',
                    payload={'status': 'completed', 'run_id': None}, occurred_at=datetime.now(UTC)))
            async def checkpoint(self): return ()
            async def stop(self, reason, **kwargs): stopped.append(reason)
            def snapshot(self): return SessionRuntimeSnapshot((), 0, 0, 0, 0, None, None)
        class Client:
            async def bind(self, request, view): return {'binding_id': view.execution_id}
            async def finalize(self, *args): return {'finalized': True, 'report_cursor': 0}
            async def aclose(self): pass
        config = make_config(tmp_path/'data').model_copy(update={
            'argus': make_config(tmp_path/'data').argus.model_copy(update={'enabled': True, 'workspace': tmp_path/'workspace'})})
        runtime = AgentRuntime(config_loader=lambda: config, session_factory=Session, handoff_client=Client())
        await runtime.start()
        try:
            first = await runtime.scoped_submit(request_v2())
            await runtime.scoped_executions.drain()
            entry = next(iter(runtime._entries.values()))
            assert (await runtime.scoped_status(wake_id=WAKE)).release_complete
            assert stopped == [] and entry.runtime is created[0]
            second = await runtime.scoped_submit(request_v2(wake_id=str(UUID(int=1201)), session_id=first.session_id))
            await runtime.scoped_executions.drain()
            assert first.session_id == second.session_id
            assert submitted == [first.task_id, second.task_id] and len(created) == 1
            await runtime.scoped_release(WAKE)
            assert entry.runtime is created[0] and stopped == []
        finally:
            await runtime.stop()
        assert stopped == ['runtime_shutdown']
    asyncio.run(run())


def test_scoped_reuse_waits_for_blocked_previous_runtime_unload(tmp_path):
    from agent_app.application.agent_runtime import AgentRuntime
    from apps.agent.test.agent_app.application.test_scoped_execution import request_v2
    from apps.agent.test.agent_app.application.test_session_runtime import make_config
    from icarus.core.contracts.input import InputAccepted
    from icarus.core.runtime_update import RuntimeUpdate
    from icarus.core.session import SessionRuntimeSnapshot
    from datetime import UTC, datetime
    from uuid import UUID

    async def run():
        created, submissions = [], []
        stop_entered, allow_stop, second_bound, allow_second_exit = (asyncio.Event() for _ in range(4))
        second_request = None
        class Session:
            def __init__(self, identity, *, publish_update, **kwargs):
                self.identity, self.publish_update = identity, publish_update
                self.stopping, self.task_id = False, None
                created.append(self)
            async def start(self): pass
            async def submit(self, prompt, *, task_id):
                if self.stopping:
                    raise RuntimeError('Cannot submit into stopping runtime')
                self.task_id = task_id
                submissions.append((self, task_id))
                return InputAccepted(task_id, 0)
            async def drain_execution(self):
                if self is not created[0]:
                    await allow_second_exit.wait()
                await self.publish_update(RuntimeUpdate(workspace_key=self.identity.workspace_key,
                    session_id=self.identity.session_id, task_id=self.task_id, type='task.finished',
                    payload={'status': 'completed', 'run_id': None}, occurred_at=datetime.now(UTC)))
            async def checkpoint(self): return ()
            async def stop(self, reason, **kwargs):
                self.stopping = True
                if self is created[0]:
                    stop_entered.set()
                    await allow_stop.wait()
            async def cancel_task(self, *args): allow_second_exit.set()
            def snapshot(self): return SessionRuntimeSnapshot((), 0, 0, 0, 0, None, None)
        class Client:
            async def bind(self, request, view):
                if request.wake_id != WAKE:
                    second_bound.set()
                return {'binding_id': view.execution_id}
            async def finalize(self, *args): return {'finalized': True, 'report_cursor': 0}
            async def aclose(self): pass
        config = make_config(tmp_path/'data').model_copy(update={
            'argus': make_config(tmp_path/'data').argus.model_copy(update={'enabled': True, 'workspace': tmp_path/'workspace'})})
        runtime = AgentRuntime(config_loader=lambda: config, session_factory=Session, handoff_client=Client())
        await runtime.start()
        try:
            first = await runtime.scoped_submit(request_v2())
            async with asyncio.timeout(2):
                while not (await runtime.scoped_status(wake_id=WAKE)).release_complete:
                    await asyncio.sleep(0)
            entry = next(iter(runtime._entries.values()))
            # Simulate an already-started legacy/lifecycle unload as well as the
            # old automatic release path. Both use the actual registry stop task.
            async with entry.mutation_lock:
                unload = entry.unload_task or runtime._begin_unload_locked(entry, 'idle_cleanup')
            await asyncio.wait_for(stop_entered.wait(), 1)
            second_request = request_v2(wake_id=str(UUID(int=1202)), session_id=first.session_id)
            pending = asyncio.create_task(runtime.scoped_submit(second_request))
            await asyncio.wait_for(second_bound.wait(), 1)
            done, _ = await asyncio.wait({pending}, timeout=0.05)
            assert not done, 'W2 must wait for teardown, not fail or submit into the stopping W1 runtime'
            assert not entry.mutation_lock.locked()
            assert submissions == [(created[0], first.task_id)]
            owner = await runtime.scoped_status(scope=second_request.scope)
            assert owner.wake_id == second_request.wake_id and not owner.execution_stopped
            allow_stop.set()
            await asyncio.wait_for(unload, 1)
            second = await asyncio.wait_for(pending, 1)
            assert len(created) == 2 and submissions[-1] == (created[1], second.task_id)
            assert entry.runtime is created[1] and not created[1].stopping
            assert await runtime.scoped_release(WAKE) == await runtime.scoped_status(wake_id=WAKE)
            assert entry.runtime is created[1] and not created[1].stopping
            retried = await runtime.scoped_submit(second_request)
            assert retried.execution_id == second.execution_id and len(submissions) == 2
            allow_second_exit.set()
            await asyncio.wait_for(runtime.scoped_executions.drain(), 2)
            assert (await runtime.scoped_status(wake_id=second.wake_id)).release_complete
            assert entry.runtime is created[1] and not created[1].stopping
        finally:
            allow_stop.set()
            allow_second_exit.set()
            if 'pending' in locals():
                await asyncio.gather(pending, return_exceptions=True)
            await runtime.stop()
    asyncio.run(run())


def test_controller_requires_durable_terminal_proof_before_release(tmp_path):
    async def run():
        store = SessionStore(tmp_path/'data')
        await store.start()
        finalized = []
        class Client:
            async def bind(self, request, view): return {'binding_id':'binding'}
            async def finalize(self, *args): finalized.append(1); return {'finalized':True,'report_cursor':0}
        class Session:
            async def drain_execution(self): pass
        async def submit(*args, **kwargs): return Session()
        async def flush(): pass
        async def evidence(identity, task_id): raise RuntimeError('persistence unavailable')
        controller = ScopedExecutionController(store, Client(), workspace=tmp_path/'workspace',
            submit_reserved=submit, flush_updates=flush, stop_evidence=evidence)
        try:
            await controller.submit(request())
            await controller.drain()
            view = await controller.status(wake_id=WAKE)
            assert not view.execution_stopped and not view.release_complete
            assert finalized == []
        finally:
            await controller.close()
            await store.close()
    asyncio.run(run())


def test_durable_checkpoint_failure_is_the_exit_kind_not_raw_completed(tmp_path):
    async def run():
        store = SessionStore(tmp_path/'data')
        await store.start()
        finalized = []
        class Client:
            async def bind(self, request, view): return {'binding_id':'binding'}
            async def finalize(self, wake, binding, kind, cursor): finalized.append(kind); return {'finalized':True,'report_cursor':0}
        class Session:
            async def drain_execution(self): pass
            def execution_exit_kind(self, task_id): return 'completed'
        async def submit(*args, **kwargs): return Session()
        async def flush(): pass
        async def evidence(identity, task_id): return 'failed'
        controller = ScopedExecutionController(store, Client(), workspace=tmp_path/'workspace',
            submit_reserved=submit, flush_updates=flush, stop_evidence=evidence)
        try:
            await controller.submit(request())
            await controller.drain()
            assert finalized == ['failed']
            assert (await controller.status(wake_id=WAKE)).exit_kind == 'failed'
        finally:
            await controller.close()
            await store.close()
    asyncio.run(run())
