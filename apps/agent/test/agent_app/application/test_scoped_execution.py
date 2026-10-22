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
            assert created[0].context.metadata["binding"]["binding_id"]
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
        finalizes = []
        class Client:
            async def bind(self, request, view): return {'binding_id': 'binding'}
            async def finalize(self, *args): finalizes.append(1); return {'finalized': True, 'report_cursor': 0}
        async def submit(*args, **kwargs): raise RuntimeError('could fail after queue acceptance')
        async def flush(): pass
        controller = ScopedExecutionController(store, Client(), workspace=tmp_path/'workspace', submit_reserved=submit, flush_updates=flush)
        try:
            with pytest.raises(ScopedExecutionError, match='execution_unknown'):
                await controller.submit(request())
            view = await controller.status(wake_id=WAKE)
            assert not view.execution_stopped and not view.release_complete
            assert finalizes == []
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
