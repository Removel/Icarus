"""Durable scope ownership, bounded admission and private session metadata."""

import asyncio
import sqlite3
from uuid import UUID

import pytest

from agent_app.application.repositories.session_store import SessionStore
from icarus.core.identity import SessionIdentity


WAKE = "10000000-0000-4000-8000-000000000001"
BOOT = "20000000-0000-4000-8000-000000000001"
EXECUTION = "30000000-0000-4000-8000-000000000001"
SESSION = "40000000-0000-4000-8000-000000000001"
TASK = "50000000-0000-4000-8000-000000000001"
ITEM = "60000000-0000-4000-8000-000000000001"


def request(*, wake_id=WAKE, target="42", reason="direct question"):
    from agent_app.application.models.scoped_execution import ScopedWakeRequest

    return ScopedWakeRequest.model_validate({
        "protocol_version": 1,
        "wake_id": wake_id,
        "origin_boot_id": BOOT,
        "scope": {"source": "qq", "account_id": "7", "chat_kind": "group", "external_id": target},
        "reason": reason,
        "rule_id": None,
        "items": [{
            "observation_id": ITEM,
            "source": "qq",
            "chat_id": f"qq:7:group:{target}",
            "message_id": "88",
            "revision": 1,
            "locator": {"account_id": "7", "chat_kind": "group", "external_id": target,
                        "message_id": "88", "time": 10},
            "excerpt": "What happened?",
            "truncated": False,
        }],
    })


async def reserve(store, tmp_path, value=None, *, ordinal=1, maximum=8):
    identity = SessionIdentity.create(tmp_path / f"workspace-{ordinal}", str(UUID(int=100 + ordinal)))
    return await store.reserve_scoped(
        value or request(), identity=identity,
        execution_id=str(UUID(int=200 + ordinal)), task_id=str(UUID(int=300 + ordinal)),
        max_active_executions=maximum,
    )


def test_scoped_identity_survives_restart_and_conflicting_resubmit(tmp_path):
    async def run():
        from agent_app.application.models.scoped_execution import ScopedExecutionError

        store = SessionStore(tmp_path / "data")
        await store.start()
        first = await reserve(store, tmp_path)
        same = await reserve(store, tmp_path, ordinal=2)
        assert same == first
        with pytest.raises(ScopedExecutionError) as error:
            await reserve(store, tmp_path, request(reason="different"), ordinal=3)
        assert error.value.code == "submission_conflict"
        await store.close()

        restored = SessionStore(tmp_path / "data")
        await restored.start()
        try:
            assert await restored.get_scoped(wake_id=WAKE) == first
            assert await restored.get_scoped(scope=request().scope) == first
        finally:
            await restored.close()

    asyncio.run(run())


def test_scope_and_global_capacity_are_atomic(tmp_path):
    async def run():
        from agent_app.application.models.scoped_execution import ScopedExecutionError

        store = SessionStore(tmp_path / "data")
        await store.start()
        try:
            results = await asyncio.gather(
                reserve(store, tmp_path, ordinal=1, maximum=1),
                reserve(store, tmp_path, request(wake_id=str(UUID(int=901)), target="43"),
                        ordinal=2, maximum=1),
                return_exceptions=True,
            )
            admitted = [value for value in results if not isinstance(value, Exception)]
            rejected = [value for value in results if isinstance(value, ScopedExecutionError)]
            assert len(admitted) == len(rejected) == 1
            assert rejected[0].code == "capacity_busy"
            with sqlite3.connect(store.database_path) as db:
                assert db.execute("SELECT count(*) FROM sessions").fetchone()[0] == 1

            owner = admitted[0]
            await store.update_scoped(owner.wake_id, {
                "phase": "stopped", "execution_stopped": True,
                "exit_kind": "cancelled", "sync_state": "pending_sync",
            })
            with pytest.raises(ScopedExecutionError) as error:
                await reserve(store, tmp_path, request(wake_id=str(UUID(int=902)), target="44"),
                              ordinal=3, maximum=1)
            assert error.value.code == "capacity_busy"
            with pytest.raises(ScopedExecutionError) as error:
                await store.complete_scoped_release(owner.wake_id)
            assert error.value.code == "busy"
            await store.update_scoped(owner.wake_id, {"sync_state": "finalized"})
            released = await store.complete_scoped_release(owner.wake_id)
            assert released.release_complete is True
            assert await store.get_scoped(scope=owner.scope) is None
            assert (await store.get_scoped(wake_id=owner.wake_id)).execution_id == owner.execution_id
            admitted_again = await reserve(
                store, tmp_path, request(wake_id=str(UUID(int=903)), target="44"),
                ordinal=4, maximum=1,
            )
            assert admitted_again.phase == "preparing"
        finally:
            await store.close()

    asyncio.run(run())


def test_same_scope_is_exclusive_across_workspaces(tmp_path):
    async def run():
        from agent_app.application.models.scoped_execution import ScopedExecutionError

        store = SessionStore(tmp_path / "data")
        await store.start()
        try:
            first = await reserve(store, tmp_path)
            with pytest.raises(ScopedExecutionError) as error:
                await reserve(store, tmp_path, request(wake_id=str(UUID(int=904))), ordinal=2)
            assert error.value.code == "scope_busy"
            assert error.value.owner.execution_id == first.execution_id
        finally:
            await store.close()

    asyncio.run(run())


def test_legacy_sessions_migrate_without_losing_records(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    identity = SessionIdentity.create(tmp_path / "workspace", "legacy")
    with sqlite3.connect(data / "icarus.db") as db:
        db.executescript("""
            CREATE TABLE workspaces (workspace_key VARCHAR(64) PRIMARY KEY,
                workspace_path VARCHAR NOT NULL, created_at DATETIME NOT NULL, last_seen_at DATETIME NOT NULL);
            CREATE TABLE sessions (workspace_key VARCHAR(64) NOT NULL, session_id VARCHAR(255) NOT NULL,
                created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL, first_user_input VARCHAR(256),
                last_public_activity_at DATETIME, last_sequence INTEGER NOT NULL,
                deleted_at DATETIME, delete_reason VARCHAR(64), PRIMARY KEY(workspace_key, session_id));
        """)
        db.execute("INSERT INTO workspaces VALUES (?, ?, ?, ?)", (
            identity.workspace_key, str(identity.workspace_path), "2026-10-22 00:00:00", "2026-10-22 00:00:00"))
        db.execute("INSERT INTO sessions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", (
            identity.workspace_key, identity.session_id, "2026-10-22 00:00:00", "2026-10-22 00:00:00",
            "kept", None, 0, None, None))

    async def run():
        store = SessionStore(data)
        await store.start()
        try:
            record = await store.get_session(identity)
            assert record.first_user_input == "kept"
            assert record.context.kind == "interactive"
            assert record.scope_key is None
            with sqlite3.connect(store.database_path) as db:
                columns = {row[1] for row in db.execute("PRAGMA table_info(sessions)")}
                assert {"scope_key", "properties"} <= columns
                assert db.execute("SELECT count(*) FROM sessions").fetchone()[0] == 1
        finally:
            await store.close()

    asyncio.run(run())


def test_background_sessions_are_absent_from_both_public_store_lists(tmp_path):
    from datetime import UTC, datetime
    from icarus.core.runtime_update import RuntimeUpdate

    async def run():
        store = SessionStore(tmp_path / "data")
        await store.start()
        try:
            background = await reserve(store, tmp_path)
            identity = SessionIdentity.create(tmp_path / "workspace-1", background.session_id)
            await store.append_update(identity, RuntimeUpdate(
                workspace_key=identity.workspace_key, session_id=identity.session_id,
                type="user.message", task_id=background.task_id,
                payload={"text": "private observation", "resources": []}, occurred_at=datetime.now(UTC),
            ))
            assert await store.list_session_ids(identity.workspace_key) == ()
            assert await store.list_session_summaries(identity.workspace_key) == ()
        finally:
            await store.close()

    asyncio.run(run())


@pytest.mark.parametrize("operation", [
    "status", "history", "history_page", "submit", "cancel", "steer", "unload", "delete", "discard", "title", "task_status", "subscribe_guard",
])
def test_public_session_methods_reject_background_identity(tmp_path, operation):
    from agent_app.application.agent_runtime import AgentRuntime
    from agent_app.application.errors import SessionNotFoundError
    from agent_app.config import ConfigModel

    async def run():
        model = {"model_name": "fixture", "context_window": 10000, "max_tokens": 100,
                 "temperature": 0.0, "default_think_level": "low"}
        config = ConfigModel(icarus_data_dir=tmp_path / "data", openai_base_url="https://invalid.example",
                             anthropic_base_url="https://invalid.example",
                             model_settings={"thinking": model, "perception": model})
        runtime = AgentRuntime(config_loader=lambda: config, data_dir=tmp_path / "data")
        await runtime.start()
        try:
            view = await reserve(runtime._store(), tmp_path)
            workspace, session = tmp_path / "workspace-1", view.session_id
            operations = {
                "status": lambda: runtime.get_session_status(workspace, session),
                "history": lambda: runtime.get_session_history(workspace, session),
                "history_page": lambda: runtime.get_session_history_page(workspace, session),
                "submit": lambda: runtime.submit(workspace, session, "injected", submission_id="injected"),
                "cancel": lambda: runtime.cancel_task(workspace, session, view.task_id),
                "steer": lambda: runtime.steer_task(workspace, session, view.task_id, "injected"),
                "unload": lambda: runtime.unload_session(workspace, session),
                "delete": lambda: runtime.delete_session(workspace, session),
                "discard": lambda: runtime.discard_empty_session(workspace, session),
                "title": lambda: runtime.generate_session_title(workspace, session),
                "subscribe_guard": lambda: runtime.ensure_public_session(
                    workspace_key=SessionIdentity.create(workspace, session).workspace_key, session_id=session),
            }
            if operation == "task_status":
                with pytest.raises((SessionNotFoundError, KeyError)):
                    runtime.get_task_status(workspace, session, view.task_id)
            else:
                with pytest.raises(SessionNotFoundError):
                    await operations[operation]()
        finally:
            await runtime.stop()

    asyncio.run(run())


def test_public_lists_hide_loaded_background_entries_and_task_status(tmp_path):
    from agent_app.application.agent_runtime import AgentRuntime
    from agent_app.application.models.status import TaskStatus
    from agent_app.config import ConfigModel
    from datetime import UTC, datetime

    async def run():
        model = {"model_name": "fixture", "context_window": 10000, "max_tokens": 100,
                 "temperature": 0.0, "default_think_level": "low"}
        config = ConfigModel(icarus_data_dir=tmp_path / "data", openai_base_url="https://invalid.example",
                             anthropic_base_url="https://invalid.example",
                             model_settings={"thinking": model, "perception": model})
        runtime = AgentRuntime(config_loader=lambda: config)
        await runtime.start()
        try:
            view = await reserve(runtime._store(), tmp_path)
            identity = SessionIdentity.create(tmp_path / "workspace-1", view.session_id)
            entry = await runtime._entry(identity)
            runtime._remember_task(entry, TaskStatus(
                workspace_key=identity.workspace_key, session_id=identity.session_id, task_id=view.task_id,
                lifecycle="running", updated_at=datetime.now(UTC),
            ))
            assert await runtime.list_session_statuses(identity.workspace_path) == ()
            from agent_app.application.errors import SessionNotFoundError
            with pytest.raises((KeyError, SessionNotFoundError)):
                runtime.get_task_status(identity.workspace_path, identity.session_id, view.task_id)
        finally:
            await runtime.stop()

    asyncio.run(run())


def test_background_runtime_inherits_configured_mcp_servers_without_mutating_config(tmp_path):
    from agent_app.application.session_runtime import SessionRuntime
    from agent_app.config import ConfigModel
    from icarus.core.session import SessionContext

    model = {"model_name": "fixture", "context_window": 10000, "max_tokens": 100,
             "temperature": 0.0, "default_think_level": "low"}
    config = ConfigModel(icarus_data_dir=tmp_path / "data", openai_base_url="https://invalid.example",
                         anthropic_base_url="https://invalid.example",
                         model_settings={"thinking": model, "perception": model},
                         mcpServers={"qq": {"command": "must-not-start"}})

    async def publish(_):
        pass

    identity = SessionIdentity.create(tmp_path / "workspace", SESSION)
    background = SessionRuntime(identity, config=config, publish_update=publish,
                                context=SessionContext(kind="argus_background", scope=request().scope.model_dump()))
    interactive = SessionRuntime(SessionIdentity.create(tmp_path / "other", "interactive"),
                                 config=config, publish_update=publish)
    assert background.config.mcp_servers == {"qq": {"command": "must-not-start"}}
    assert interactive.config.mcp_servers == {"qq": {"command": "must-not-start"}}
    assert config.mcp_servers == {"qq": {"command": "must-not-start"}}
    assert background.context.kind == "argus_background"


def test_session_drain_waits_for_graph_then_actual_tools_and_survives_waiter_cancel(tmp_path):
    from agent_app.application.session_runtime import SessionRuntime
    from agent_app.config import ConfigModel
    from concurrent.futures import Future

    async def run():
        model = {"model_name": "fixture", "context_window": 10000, "max_tokens": 100,
                 "temperature": 0.0, "default_think_level": "low"}
        config = ConfigModel(icarus_data_dir=tmp_path / "data", openai_base_url="https://invalid.example",
                             anthropic_base_url="https://invalid.example",
                             model_settings={"thinking": model, "perception": model})
        graph_entered, graph_release = asyncio.Event(), asyncio.Event()

        async def publish(_):
            pass

        runtime = SessionRuntime(SessionIdentity.create(tmp_path / "workspace", SESSION),
                                 config=config, publish_update=publish)

        async def graph_drain():
            graph_entered.set()
            await graph_release.wait()

        runtime.plugin_manager.drain = graph_drain
        completion = Future()
        runtime.tool_execution_tracker.track(completion)
        waiter = asyncio.create_task(runtime.drain_execution())
        await graph_entered.wait()
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        graph_release.set()
        second = asyncio.create_task(runtime.drain_execution())
        await asyncio.sleep(0)
        assert not second.done()
        completion.set_result(None)
        await asyncio.wait_for(second, 1)
        assert runtime.tool_execution_tracker.pending_count == 0

    asyncio.run(run())


def test_update_flush_waits_for_persistence_queue():
    from agent_app.application.services.update_pipeline import UpdatePipeline

    async def run():
        queue = asyncio.Queue()
        queue.put_nowait(object())
        pipeline = UpdatePipeline(
            begin_unload_locked=None, clock=None, entries={}, has_nonterminal_tasks=None,
            lifecycle_update=None, remember_task=None, store=None, update_queue=queue,
            updates=None, logger=None,
        )
        waiter = asyncio.create_task(pipeline.flush())
        await asyncio.sleep(0)
        assert not waiter.done()
        queue.get_nowait()
        queue.task_done()
        await asyncio.wait_for(waiter, 1)

    asyncio.run(run())


def test_internal_outbox_rows_are_durable_but_not_public_history(tmp_path):
    from datetime import UTC, datetime
    from icarus.core.runtime_update import RuntimeUpdate

    async def run():
        store = SessionStore(tmp_path / "data")
        await store.start()
        identity = SessionIdentity.create(tmp_path / "workspace", "public")
        try:
            await store.create_session(identity)
            first = await store.append_update(identity, RuntimeUpdate(
                workspace_key=identity.workspace_key, session_id=identity.session_id,
                type="user.message", task_id="task", payload={"text": "hello", "resources": []},
                occurred_at=datetime.now(UTC),
            ))
            sequence = await store.append_internal(identity, task_id="task", update_type="argus_report",
                                                   payload={"report_id": "report", "items": []})
            assert sequence == 2
            rows = await store.read_internal(identity, update_types={"argus_report"})
            assert len(rows) == 1 and rows[0].payload["report_id"] == "report"
            public, cursor = await store.read_updates(identity)
            assert public == (first,)
            assert cursor == 2
            assert await store.read_updates(identity, after_sequence=1) == ((), 2)
        finally:
            await store.close()
        restored = SessionStore(tmp_path / "data")
        await restored.start()
        try:
            assert len(await restored.read_internal(identity, update_types={"argus_report"})) == 1
        finally:
            await restored.close()

    asyncio.run(run())


def test_controller_binds_before_submit_and_keeps_scope_until_actual_exit(tmp_path):
    async def run():
        from agent_app.application.controllers.scoped_execution import ScopedExecutionController

        store = SessionStore(tmp_path / "data")
        await store.start()
        bound, allow_bind, submitted, allow_exit = (asyncio.Event() for _ in range(4))
        order = []

        class Service:
            async def bind(self, value, view):
                bound.set()
                await allow_bind.wait()
                order.append("bound")
                return {"binding_id": "70000000-0000-4000-8000-000000000001", "initial_items": [], "initial_cursor": "cursor"}

            async def finalize(self, *args):
                order.append("finalized")
                return {"finalized": True, "report_cursor": 0}

        class Session:
            async def drain_execution(self):
                await allow_exit.wait()

            async def cancel_task(self, *args):
                order.append("cancel_requested")

        async def submit_reserved(identity, task_id, prompt, *, execution_id):
            order.append("submitted")
            submitted.set()
            return Session()

        async def flush():
            order.append("flushed")

        controller = ScopedExecutionController(store, Service(), workspace=tmp_path / "workspace",
                                                submit_reserved=submit_reserved, flush_updates=flush)
        try:
            submission = asyncio.create_task(controller.submit(request()))
            await bound.wait()
            assert not submitted.is_set()
            assert (await controller.status(wake_id=WAKE)).phase == "preparing"
            allow_bind.set()
            view = await submission
            assert view.execution_stopped is False
            await submitted.wait()
            cancelled = await controller.cancel(WAKE, "test")
            assert cancelled.cancel_requested and not cancelled.execution_stopped
            assert (await controller.status(scope=request().scope)).execution_id == view.execution_id
            allow_exit.set()
            await asyncio.wait_for(controller.drain(), 1)
            stopped = await controller.status(wake_id=WAKE)
            assert stopped.release_complete and stopped.execution_stopped
            assert await controller.status(scope=request().scope) is None
            assert order.index("bound") < order.index("submitted") < order.index("flushed") < order.index("finalized")
            assert (await controller.submit(request())).execution_id == view.execution_id
        finally:
            allow_bind.set()
            allow_exit.set()
            await controller.close()
            await store.close()

    asyncio.run(run())


def test_rejected_reports_do_not_block_finalize_and_restart_never_resubmits(tmp_path):
    async def run():
        from agent_app.application.controllers.scoped_execution import ScopedExecutionController

        store = SessionStore(tmp_path / "data")
        await store.start()
        view = await reserve(store, tmp_path)
        record = await store.scoped_record(WAKE)
        await store.update_scoped(WAKE, {
            "binding": {"binding_id": "70000000-0000-4000-8000-000000000001"},
            "bound": True, "launching": True,
        })
        await store.append_internal(record.identity, task_id=view.task_id, update_type="argus_report", payload={
            "report_id": "rejected", "items": [{"observation_id": ITEM, "status": "handled"}],
        })
        calls = []

        class Service:
            async def send_report(self, *args):
                calls.append("report")
                return {"report_id": "rejected", "disposition": "rejected", "code": "item_not_reportable", "report_cursor": 0}

            async def finalize(self, wake, binding, kind, cursor):
                calls.append(("finalize", kind, cursor))
                return {"finalized": True, "report_cursor": cursor}

        async def must_not_submit(*args, **kwargs):
            pytest.fail("Restart must not run old model work")

        async def flush():
            pass

        controller = ScopedExecutionController(store, Service(), workspace=tmp_path / "workspace",
                                                submit_reserved=must_not_submit, flush_updates=flush)
        try:
            await controller.recover()
            restored = await controller.status(wake_id=WAKE)
            assert restored.execution_stopped and restored.release_complete
            assert restored.exit_kind == "interrupted"
            assert restored.report_cursor == 0
            assert calls == ["report", ("finalize", "interrupted", 0)]
            rejected = await store.read_internal(record.identity, update_types={"argus_report_rejected"})
            assert len(rejected) == 1
        finally:
            await controller.close()
            await store.close()

    asyncio.run(run())


def test_runtime_scoped_facade_uses_reserved_task_identity_and_hidden_context(tmp_path, monkeypatch):
    from agent_app.application.agent_runtime import AgentRuntime
    from agent_app.config import ConfigModel
    from icarus.core.contracts.input import InputAccepted
    from icarus.core.session import SessionRuntimeSnapshot

    async def run():
        model = {"model_name": "fixture", "context_window": 10000, "max_tokens": 100,
                 "temperature": 0.0, "default_think_level": "low"}
        config = ConfigModel(icarus_data_dir=tmp_path / "data", openai_base_url="https://invalid.example",
                             anthropic_base_url="https://invalid.example", model_settings={"thinking": model, "perception": model},
                             argus={"enabled": True, "workspace": tmp_path / "background"})
        created, submitted = [], []
        release = asyncio.Event()

        class Session:
            def __init__(self, identity, *, config, context, **kwargs):
                self.identity, self.context = identity, context
                self.publish_update = kwargs['publish_update']
                created.append(self)
            async def start(self): pass
            async def submit(self, prompt, *, task_id):
                submitted.append(task_id)
                return InputAccepted(task_id, 0)
            async def drain_execution(self):
                await release.wait()
                from icarus.core.runtime_update import RuntimeUpdate
                from datetime import datetime, UTC
                await self.publish_update(RuntimeUpdate(workspace_key=self.identity.workspace_key,
                    session_id=self.identity.session_id, task_id=submitted[0], type='task.finished',
                    payload={'status':'completed','run_id':None}, occurred_at=datetime.now(UTC)))
            async def checkpoint(self): return ()
            async def stop(self, *args, **kwargs): pass
            def snapshot(self): return SessionRuntimeSnapshot((), 0, 0, 0, 0, None, None)

        class Service:
            async def bind(self, value, view):
                return {"binding_id": "70000000-0000-4000-8000-000000000001", "initial_items": [], "initial_cursor": "cursor"}
            async def finalize(self, *args): return {"finalized": True, "report_cursor": 0}
            async def aclose(self): pass

        runtime = AgentRuntime(config_loader=lambda: config, session_factory=Session, handoff_client=Service())
        await runtime.start()
        try:
            view = await runtime.scoped_submit(request())
            assert submitted == [view.task_id]
            assert created[0].context.kind == "argus_background"
            assert "binding" not in created[0].context.metadata
            binding = await runtime._background_facilities(created[0].identity)['load_binding'](view.task_id)
            assert binding['binding_id'] == "70000000-0000-4000-8000-000000000001"
            assert await runtime.list_session_statuses(tmp_path / "background") == ()
            release.set()
            await runtime.scoped_executions.drain()
            assert (await runtime.scoped_status(wake_id=WAKE)).release_complete
        finally:
            release.set()
            await runtime.stop()

    asyncio.run(run())


def test_confirmed_report_receipts_advance_cursor_before_finalize(tmp_path):
    async def run():
        from agent_app.application.controllers.scoped_execution import ScopedExecutionController
        store = SessionStore(tmp_path/'data')
        await store.start()
        view = await reserve(store, tmp_path)
        record = await store.scoped_record(WAKE)
        await store.update_scoped(WAKE, {'binding': {'binding_id': 'binding'}, 'phase': 'stopped',
            'execution_stopped': True, 'exit_kind': 'completed', 'sync_state': 'pending_sync'})
        await store.append_internal(record.identity, task_id=view.task_id, update_type='argus_report',
            payload={'report_id': 'one', 'items': [{'observation_id': ITEM, 'status': 'handled'}]})
        await store.append_internal(record.identity, task_id=view.task_id, update_type='argus_report_synced',
            payload={'report_id': 'one', 'disposition': 'accepted', 'report_cursor': 1})
        cursors = []
        class Client:
            async def finalize(self, wake_id, binding, kind, cursor):
                cursors.append(cursor)
                return {'finalized': True, 'report_cursor': cursor}
        async def flush(): pass
        controller = ScopedExecutionController(store, Client(), workspace=tmp_path/'workspace',
            submit_reserved=None, flush_updates=flush)
        try:
            await controller.release(WAKE)
            assert cursors == [1]
            assert (await controller.status(wake_id=WAKE)).report_cursor == 1
        finally:
            await store.close()
    asyncio.run(run())


def test_rpc_waiter_cancel_during_bind_does_not_cancel_or_duplicate_execution(tmp_path):
    async def run():
        from agent_app.application.controllers.scoped_execution import ScopedExecutionController
        store = SessionStore(tmp_path/'data')
        await store.start()
        entered, release_bind, release_run = asyncio.Event(), asyncio.Event(), asyncio.Event()
        calls = []
        class Client:
            async def bind(self, request, view):
                entered.set()
                await release_bind.wait()
                return {'binding_id': 'binding', 'initial_items': [], 'initial_cursor': 'cursor'}
            async def finalize(self, *args): return {'finalized': True, 'report_cursor': 0}
        class Session:
            async def drain_execution(self): await release_run.wait()
            async def cancel_task(self, *args): release_run.set()
        async def submit(*args, **kwargs):
            calls.append('submit')
            return Session()
        async def flush(): pass
        controller = ScopedExecutionController(store, Client(), workspace=tmp_path/'workspace',
            submit_reserved=submit, flush_updates=flush)
        try:
            waiter = asyncio.create_task(controller.submit(request()))
            await entered.wait()
            waiter.cancel()
            with pytest.raises(asyncio.CancelledError): await waiter
            release_bind.set()
            async with asyncio.timeout(1):
                while not calls: await asyncio.sleep(0)
            await controller.submit(request())
            assert calls == ['submit']
        finally:
            release_bind.set()
            release_run.set()
            await controller.close()
            await store.close()
    asyncio.run(run())


def test_monitor_retries_final_sync_without_rerunning_model(tmp_path):
    async def run():
        from agent_app.application.controllers.scoped_execution import ScopedExecutionController
        from agent_app.argus_protocol import HandoffError
        store = SessionStore(tmp_path/'data')
        await store.start()
        calls, submissions = [], []
        class Client:
            async def bind(self, request, view): return {'binding_id': 'binding'}
            async def finalize(self, *args):
                calls.append('finalize')
                if len(calls) == 1: raise HandoffError('unavailable', retryable=True)
                return {'finalized': True, 'report_cursor': 0}
        class Session:
            async def drain_execution(self): pass
        async def submit(*args, **kwargs): submissions.append(1); return Session()
        async def flush(): pass
        controller = ScopedExecutionController(store, Client(), workspace=tmp_path/'workspace',
            submit_reserved=submit, flush_updates=flush, retry_seconds=0.01)
        try:
            await controller.submit(request())
            await asyncio.wait_for(controller.drain(), 1)
            assert (await controller.status(wake_id=WAKE)).release_complete
            assert len(calls) == 2 and len(submissions) == 1
        finally:
            await controller.close()
            await store.close()
    asyncio.run(run())


def test_launch_exception_without_stop_evidence_cannot_finalize_or_release(tmp_path):
    async def run():
        from agent_app.application.controllers.scoped_execution import ScopedExecutionController
        from agent_app.application.models.scoped_execution import ScopedExecutionError
        store = SessionStore(tmp_path/'data')
        await store.start()
        finalizes, submissions = [], []
        class Client:
            async def bind(self, request, view): return {'binding_id': 'binding'}
            async def finalize(self, *args): finalizes.append(1); return {'finalized': True, 'report_cursor': 0}
        async def submit(*args, **kwargs):
            submissions.append(1)
            raise RuntimeError('could fail after queue acceptance')
        async def flush(): pass
        controller = ScopedExecutionController(store, Client(), workspace=tmp_path/'workspace', submit_reserved=submit, flush_updates=flush)
        try:
            with pytest.raises(ScopedExecutionError, match='execution_unknown'):
                await controller.submit(request())
            view = await controller.status(wake_id=WAKE)
            assert not view.execution_stopped and not view.release_complete
            assert finalizes == []
            retried = await controller.submit(request())
            assert retried.execution_id == view.execution_id and retried.task_id == view.task_id
            assert submissions == [1]
            assert (await store.scoped_record(WAKE)).properties['launching'] is True
            assert not controller._monitors
        finally:
            await controller.close()
            await store.close()
    asyncio.run(run())


def test_shutdown_without_qq_configuration_can_query_persisted_execution(tmp_path):
    async def run():
        from agent_app.application.agent_runtime import AgentRuntime
        from agent_app.config import ConfigModel
        store = SessionStore(tmp_path/'data')
        await store.start()
        await reserve(store, tmp_path)
        await store.close()
        model = {'model_name':'fixture','context_window':10000,'max_tokens':100,'temperature':0.0,'default_think_level':'low'}
        config = ConfigModel(icarus_data_dir=tmp_path/'data', openai_base_url='https://invalid.example',
            anthropic_base_url='https://invalid.example', model_settings={'thinking':model,'perception':model})
        runtime = AgentRuntime(config_loader=lambda:config)
        await runtime.start()
        try:
            view = await runtime.scoped_status(wake_id=WAKE)
            assert view.execution_id
        finally:
            await runtime.stop()
    asyncio.run(run())


def test_frozen_json_fixture_has_cross_app_canonical_fingerprint():
    import json
    from pathlib import Path
    from agent_app.application.models.scoped_execution import ScopedWakeRequest
    fixture = json.loads((Path(__file__).parent/'fixtures/argus_v1.json').read_text())
    value = ScopedWakeRequest.model_validate(fixture['request'])
    assert value.fingerprint == fixture['fingerprint']
    changed = json.loads(json.dumps(fixture['request']))
    changed['items'][0]['locator']['time'] = 10.0
    assert ScopedWakeRequest.model_validate(changed).fingerprint == fixture['fingerprint']


async def finish_reservation(store, wake_id):
    await store.update_scoped(wake_id, {"phase": "stopped", "execution_stopped": True,
        "exit_kind": "completed", "sync_state": "finalized"})
    return await store.complete_scoped_release(wake_id)


def request_v2(*, wake_id=WAKE, session_id=None, target="42"):
    from agent_app.application.models.scoped_execution import ScopedWakeRequest
    return ScopedWakeRequest.model_validate({**request(wake_id=wake_id, target=target).model_dump(mode="json"),
        "protocol_version": 2, "target_session_id": session_id})


def test_semantic_reuse_preserves_prior_wake_identity_and_reports(tmp_path):
    async def run():
        from agent_app.application.models.scoped_execution import ScopedExecutionError
        store = SessionStore(tmp_path / "data")
        await store.start()
        try:
            first = await reserve(store, tmp_path)
            record = await store.scoped_record(WAKE)
            await store.update_scoped(WAKE, {"binding": {"binding_id": "old-binding"}})
            await store.append_internal(record.identity, task_id=first.task_id, update_type="argus_report",
                payload={"report_id": "old-report", "items": [], "fingerprint": "unchanged"})
            old = await finish_reservation(store, WAKE)
            second_request = request(wake_id=str(UUID(int=910)))
            second = await store.reserve_scoped(second_request, identity=record.identity,
                execution_id=str(UUID(int=911)), task_id=str(UUID(int=912)))
            assert first.session_id == second.session_id
            assert await store.get_scoped(wake_id=WAKE) == old
            assert (await store.scoped_record(WAKE)).properties["binding"]["binding_id"] == "old-binding"
            await store.complete_scoped_release(WAKE)
            assert await store.get_scoped(scope=first.scope) == second
            reports = await store.read_internal(record.identity, update_types={"argus_report"})
            assert reports[0].payload == {"report_id": "old-report", "items": [], "fingerprint": "unchanged",
                "wake_id": WAKE, "binding_id": "old-binding"}
            stable = await store.get_session(record.identity)
            assert "binding" not in stable.context.metadata and "wake_id" not in stable.context.metadata
            assert stable.properties["current_wake_id"] == second.wake_id
            assert "request" not in stable.properties
            with pytest.raises(ScopedExecutionError, match="submission_conflict"):
                await store.reserve_scoped(request(reason="changed"), identity=record.identity,
                    execution_id=str(UUID(int=913)), task_id=str(UUID(int=914)))
            with sqlite3.connect(store.database_path) as db:
                before = db.execute("SELECT sequence, occurred_at FROM conversation_updates WHERE update_type='argus_execution'").fetchall()
            await store.update_scoped(second.wake_id, {"binding_attempted": True})
            with pytest.raises(RuntimeError, match='admission'):
                await store.update_scoped(second.wake_id, {'unrecognized_state': True})
            with pytest.raises(RuntimeError, match='admission'):
                await store.update_scoped(second.wake_id, {'binding': {'binding_id': 'new', 'session_id': SESSION}})
            with sqlite3.connect(store.database_path) as db:
                assert db.execute("SELECT sequence, occurred_at FROM conversation_updates WHERE update_type='argus_execution'").fetchall() == before
            public, cursor = await store.read_updates(record.identity)
            assert public == () and cursor == 3
        finally:
            await store.close()
    asyncio.run(run())


def test_two_sessions_one_scope_only_one_execution_owner(tmp_path):
    async def run():
        from agent_app.application.models.scoped_execution import ScopedExecutionError
        store = SessionStore(tmp_path / "data")
        await store.start()
        try:
            first = await reserve(store, tmp_path)
            identity1 = (await store.scoped_record(WAKE)).identity
            await finish_reservation(store, WAKE)
            second = await reserve(store, tmp_path, request(wake_id=str(UUID(int=920))), ordinal=2, maximum=1)
            identity2 = (await store.scoped_record(second.wake_id)).identity
            await finish_reservation(store, second.wake_id)
            results = await asyncio.gather(*(store.reserve_scoped(request(wake_id=str(UUID(int=930+i))),
                identity=identity, execution_id=str(UUID(int=940+i)), task_id=str(UUID(int=950+i)),
                max_active_executions=1) for i, identity in enumerate((identity1, identity2))), return_exceptions=True)
            assert sum(not isinstance(value, Exception) for value in results) == 1
            errors = [value for value in results if isinstance(value, ScopedExecutionError)]
            assert len(errors) == 1 and errors[0].code == "scope_busy"
        finally:
            await store.close()
    asyncio.run(run())


def test_v2_target_is_required_nullable_and_part_of_frozen_fingerprint():
    from pydantic import ValidationError
    from agent_app.application.models.scoped_execution import ScopedWakeRequest
    value = request().model_dump(mode="json")
    assert "target_session_id" not in value
    with pytest.raises(ValidationError):
        ScopedWakeRequest.model_validate({**value, "protocol_version": 2})
    with pytest.raises(ValidationError):
        ScopedWakeRequest.model_validate({**value, "target_session_id": None})
    first = request_v2()
    assert first.model_dump(mode="json")["target_session_id"] is None
    assert first.fingerprint != request_v2(session_id=SESSION).fingerprint


def test_scoped_submit_reuses_idle_session_without_changing_prior_execution(tmp_path):
    async def run():
        from agent_app.application.controllers.scoped_execution import ScopedExecutionController
        store = SessionStore(tmp_path / "data")
        await store.start()
        tasks = []
        class Client:
            async def bind(self, request, view): return {"binding_id": view.execution_id}
            async def finalize(self, *args): return {"finalized": True, "report_cursor": 0}
        class Session:
            async def drain_execution(self): pass
        async def submit(identity, task_id, prompt, *, execution_id):
            binding = await store.load_binding(identity, task_id)
            tasks.append((identity, task_id, binding))
            return Session()
        async def flush(): pass
        controller = ScopedExecutionController(store, Client(), workspace=tmp_path / "workspace",
            submit_reserved=submit, flush_updates=flush)
        try:
            first = await controller.submit(request_v2())
            await controller.drain()
            old = await controller.status(wake_id=WAKE)
            second = await controller.submit(request_v2(wake_id=str(UUID(int=960)), session_id=first.session_id))
            await controller.drain()
            assert first.session_id == second.session_id
            assert first.execution_id != second.execution_id and first.task_id != second.task_id
            assert tasks[0][2]["wake_id"] == WAKE and tasks[1][2]["wake_id"] == second.wake_id
            assert tasks[0][2]["binding_id"] != tasks[1][2]["binding_id"]
            assert await controller.status(wake_id=WAKE) == old
            with pytest.raises(Exception):
                await store.load_binding(tasks[0][0], "missing-task")
        finally:
            await controller.close()
            await store.close()
    asyncio.run(run())


def test_target_session_scope_owner_and_retirement_are_validated(tmp_path):
    async def run():
        from agent_app.application.models.scoped_execution import ScopedExecutionError
        store = SessionStore(tmp_path / "data")
        await store.start()
        try:
            first = await reserve(store, tmp_path)
            record = await store.scoped_record(WAKE)
            await finish_reservation(store, WAKE)
            interactive = SessionIdentity.create(record.identity.workspace_path, SESSION)
            await store.create_session(interactive)
            for identity, value in [
                (interactive, request_v2(wake_id=str(UUID(int=971)), session_id=SESSION)),
                (record.identity, request_v2(wake_id=str(UUID(int=972)), session_id=first.session_id, target="43")),
                (SessionIdentity.create(tmp_path / "wrong-workspace", first.session_id), request_v2(wake_id=str(UUID(int=973)), session_id=first.session_id)),
                (SessionIdentity.create(record.identity.workspace_path, str(UUID(int=999))), request_v2(wake_id=str(UUID(int=974)), session_id=str(UUID(int=999)))),
            ]:
                with pytest.raises(ScopedExecutionError, match="target_unavailable"):
                    await store.reserve_scoped(value, identity=identity, execution_id=EXECUTION, task_id=TASK)
            with sqlite3.connect(store.database_path) as db:
                db.execute("UPDATE sessions SET properties=json_set(properties, '$.retired_at', '2026-10-25T00:00:00+00:00') WHERE session_id=?", (first.session_id,))
            with pytest.raises(ScopedExecutionError, match="target_unavailable"):
                await store.reserve_scoped(request_v2(wake_id=str(UUID(int=975)), session_id=first.session_id), identity=record.identity,
                    execution_id=EXECUTION, task_id=TASK)
        finally:
            await store.close()
    asyncio.run(run())


def test_argus_upgrade_preserves_table_set_and_legacy_outbox(tmp_path):
    import json
    async def run():
        store = SessionStore(tmp_path / "data")
        await store.start()
        view = await reserve(store, tmp_path)
        identity = (await store.scoped_record(WAKE)).identity
        await store.update_scoped(WAKE, {"binding": {"binding_id": "legacy-binding"}, "binding_attempted": True,
            "phase": "stopped", "execution_stopped": True, "exit_kind": "interrupted", "sync_state": "pending_sync"})
        # Explicit pre-upgrade shape, independent of the current reservation implementation.
        legacy = {**view.model_dump(mode="json"), "kind": "argus_background", "owner": "argus",
            "fingerprint": request().fingerprint, "request": request().model_dump(mode="json"),
            "binding": {"binding_id": "legacy-binding"}, "binding_attempted": True,
            "phase": "stopped", "execution_stopped": True, "exit_kind": "interrupted", "sync_state": "pending_sync"}
        await store.close()
        with sqlite3.connect(store.database_path) as db:
            db.execute("DELETE FROM conversation_updates")
            db.execute("UPDATE sessions SET properties=?, last_sequence=2", (json.dumps(legacy),))
            db.execute("INSERT INTO conversation_updates VALUES (?, ?, 1, ?, 'user.message', ?, '2026-10-25 00:00:00')",
                (identity.workspace_key, identity.session_id, view.task_id, json.dumps({"text": "kept", "resources": []})))
            db.execute("INSERT INTO conversation_updates VALUES (?, ?, 2, ?, 'argus_report', ?, '2026-10-25 00:00:00')",
                (identity.workspace_key, identity.session_id, view.task_id,
                 json.dumps({"report_id": "legacy-report", "items": [], "fingerprint": "kept"})))
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
        assert tables == {"workspaces", "sessions", "session_titles", "conversation_updates"}
        for _ in range(2):
            restored = SessionStore(tmp_path / "data")
            await restored.start()
            try:
                assert (await restored.get_scoped(wake_id=WAKE)).exit_kind == "interrupted"
                public, cursor = await restored.read_updates(identity)
                assert public[0].payload["text"] == "kept" and public[0].sequence == 1
                assert cursor == 3
                admissions = await restored.read_internal(identity, update_types={"argus_execution"})
                assert len(admissions) == 1
                reports = await restored.read_internal(identity, update_types={"argus_report"})
                assert reports[0].sequence == 2
                assert reports[0].payload == {"report_id": "legacy-report", "items": [], "fingerprint": "kept",
                    "wake_id": WAKE, "binding_id": "legacy-binding"}
                stable = await restored.get_session(identity)
                assert "request" not in stable.properties and "wake_id" not in stable.properties
                with sqlite3.connect(restored.database_path) as db:
                    assert {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")} == tables
            finally:
                await restored.close()
    asyncio.run(run())


def test_argus_upgrade_invalid_recovery_snapshot_rolls_back(tmp_path):
    import json
    async def run():
        store = SessionStore(tmp_path / "data")
        await store.start()
        await reserve(store, tmp_path)
        await store.close()
        invalid = {"kind": "argus_background", "owner": "argus", "wake_id": WAKE,
            "scope": request().scope.model_dump(mode="json")}
        with sqlite3.connect(store.database_path) as db:
            db.execute("DELETE FROM conversation_updates")
            db.execute("UPDATE sessions SET properties=?, last_sequence=0", (json.dumps(invalid),))
        broken = SessionStore(tmp_path / "data")
        with pytest.raises(RuntimeError, match="admission"):
            await broken.start()
        with sqlite3.connect(store.database_path) as db:
            assert json.loads(db.execute("SELECT properties FROM sessions").fetchone()[0]) == invalid
            assert db.execute("SELECT count(*) FROM conversation_updates").fetchone()[0] == 0
    asyncio.run(run())


def test_trusted_candidates_and_notify_are_scope_bound_and_do_not_launch(tmp_path):
    from agent_app.application.agent_runtime import AgentRuntime
    from agent_app.application.models.scoped_execution import ScopedExecutionError
    from apps.agent.test.agent_app.application.test_session_runtime import make_config
    from agent_app.argus_protocol import HandoffError
    async def run():
        runtime = AgentRuntime(config_loader=lambda: make_config(tmp_path/'data'))
        await runtime.start()
        class Client:
            failed = False
            async def session_pending(self, session_id, scope):
                if self.failed:
                    raise HandoffError('unavailable', retryable=True)
                return {'protocol_version': 2, 'pending_count': 1, 'last_related_at': None}
        client = Client()
        runtime._handoff_client = client
        try:
            view = await reserve(runtime._store(), tmp_path)
            identity = (await runtime._store().scoped_record(WAKE)).identity
            signal = runtime._background_facilities(identity)['availability_signal']
            assert signal is runtime._background_facilities(identity)['availability_signal']
            page = await runtime.scoped_sessions(request().scope, limit=1)
            assert page['items'][0]['session_id'] == view.session_id
            assert page['items'][0]['has_work'] is True
            assert set(page['items'][0]) == {'session_id', 'summary', 'last_related_at', 'has_work'}
            assert (await runtime.scoped_sessions(request(target='43').scope))['items'] == []
            assert await runtime.scoped_notify(wake_id=WAKE, session_id=view.session_id) == {'accepted': True, 'merged': False}
            assert signal.is_set()
            assert await runtime.scoped_notify(wake_id=WAKE, session_id=view.session_id) == {'accepted': True, 'merged': True}
            with pytest.raises(ScopedExecutionError, match='not_found'):
                await runtime.scoped_notify(wake_id=WAKE, session_id=SESSION)
            assert not runtime._entries
            with pytest.raises(ValueError): await runtime.scoped_sessions(request().scope, limit=21)
            with pytest.raises(ValueError): await runtime.scoped_sessions(request().scope, cursor='x'*2049)
            client.failed = True
            with pytest.raises(ScopedExecutionError, match='unavailable'):
                await runtime.scoped_sessions(request().scope)
        finally:
            runtime._handoff_client = None
            await runtime.stop()
    asyncio.run(run())


def test_reused_session_report_replay_keeps_each_wake_binding_namespace(tmp_path):
    from agent_app.application.controllers.scoped_execution import ScopedExecutionController
    async def run():
        store = SessionStore(tmp_path / 'data')
        await store.start()
        calls = []
        class Client:
            async def send_report(self, wake, binding, report):
                calls.append((wake, binding, report['report_id']))
                return {'report_id': report['report_id'], 'disposition': 'accepted', 'report_cursor': 1}
            async def finalize(self, wake, binding, kind, cursor):
                calls.append(('finalize', wake, binding, cursor))
                return {'finalized': True, 'report_cursor': cursor}
        async def flush(): pass
        controller = ScopedExecutionController(store, Client(), workspace=tmp_path/'workspace',
            submit_reserved=None, flush_updates=flush)
        try:
            first = await reserve(store, tmp_path)
            identity = (await store.scoped_record(WAKE)).identity
            await store.update_scoped(WAKE, {'binding': {'binding_id': 'old'}})
            await store.append_internal(identity, task_id=first.task_id, update_type='argus_report',
                payload={'report_id': 'same-report-id', 'items': []})
            await finish_reservation(store, WAKE)
            value = request(wake_id=str(UUID(int=980)))
            second = await store.reserve_scoped(value, identity=identity, execution_id=str(UUID(int=981)), task_id=str(UUID(int=982)))
            await store.update_scoped(second.wake_id, {'binding': {'binding_id': 'new'}, 'phase': 'stopped',
                'execution_stopped': True, 'exit_kind': 'completed', 'sync_state': 'pending_sync'})
            await store.append_internal(identity, task_id=second.task_id, update_type='argus_report',
                payload={'report_id': 'same-report-id', 'items': []})
            await controller.release(second.wake_id)
            assert calls == [(second.wake_id, 'new', 'same-report-id'), ('finalize', second.wake_id, 'new', 1)]
            assert (await store.get_scoped(wake_id=WAKE)).report_cursor == 0
            receipts = await store.read_internal(identity, update_types={'argus_report_synced'})
            assert len(receipts) == 1 and receipts[0].payload['wake_id'] == second.wake_id
            assert receipts[0].payload['binding_id'] == 'new'
        finally:
            await store.close()
    asyncio.run(run())


def test_frozen_v2_json_fixture_retains_nullable_target_and_canonical_fingerprint():
    import json
    from pathlib import Path
    from agent_app.application.models.scoped_execution import ScopedWakeRequest
    fixture = json.loads((Path(__file__).parent/'fixtures/argus_v2.json').read_text())
    value = ScopedWakeRequest.model_validate(fixture['request'])
    assert value.fingerprint == fixture['fingerprint']
    assert value.model_dump(mode='json') == fixture['request']


def test_session_context_is_deeply_immutable():
    from icarus.core.session import SessionContext

    metadata = {"nested": {"values": [1, 2]}}
    context = SessionContext(kind="argus_background", scope={"account_id": "7"}, metadata=metadata)
    metadata["nested"]["values"].append(3)
    assert context.metadata["nested"]["values"] == (1, 2)
    with pytest.raises(TypeError):
        context.scope["account_id"] = "other"
    with pytest.raises(TypeError):
        context.metadata["nested"]["other"] = True
