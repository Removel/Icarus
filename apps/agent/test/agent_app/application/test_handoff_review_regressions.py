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


def test_release_unloads_runtime_once_and_cleanup_failure_does_not_reoccupy(tmp_path):
    async def run():
        store = SessionStore(tmp_path/'data')
        await store.start()
        unloaded = []
        class Client:
            async def bind(self, request, view): return {'binding_id':'binding'}
            async def finalize(self, *args): return {'finalized':True,'report_cursor':0}
        class Session:
            async def drain_execution(self): pass
        async def submit(*args, **kwargs): return Session()
        async def flush(): pass
        async def unload(identity):
            unloaded.append(identity.session_id)
            raise RuntimeError('cleanup failure')
        controller = ScopedExecutionController(store, Client(), workspace=tmp_path/'workspace',
            submit_reserved=submit, flush_updates=flush, unload_session=unload)
        try:
            await controller.submit(request())
            await controller.drain()
            view = await controller.status(wake_id=WAKE)
            assert view.release_complete
            assert unloaded == [view.session_id]
            assert await controller.status(scope=request().scope) is None
        finally:
            await controller.close()
            await store.close()
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
