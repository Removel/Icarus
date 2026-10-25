"""Durable admission and handoff around the existing session execution runtime."""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from agent_app.application.models.scoped_execution import ExecutionView, ScopedExecutionError, ScopedWakeRequest
from agent_app.argus_protocol import HandoffError
from agent_app.application.semantic_context import initial_task_prompt
from agent_app.application.services.argus_pending import pending_state
from icarus.core.identity import SessionIdentity


class ScopedExecutionController:
    def __init__(self, store, client, *, workspace: Path, submit_reserved: Callable,
                 flush_updates: Callable[[], Awaitable[None]], max_active_executions=8,
                 timeout_seconds=900, retry_seconds=1, stop_evidence=None):
        self.store, self.client = store, client
        self.workspace = Path(workspace)
        self.submit_reserved, self.flush_updates = submit_reserved, flush_updates
        self.maximum, self.timeout = max_active_executions, timeout_seconds
        self.retry_seconds = retry_seconds
        self.stop_evidence = stop_evidence
        self._closing = False
        self._locks: dict[str, asyncio.Lock] = {}
        self._submissions: dict[str, asyncio.Task] = {}
        self._sessions: dict[str, Any] = {}
        self._monitors: dict[str, asyncio.Task] = {}

    async def status(self, *, wake_id=None, scope=None):
        return await self.store.get_scoped(wake_id=wake_id, scope=scope)

    async def submit(self, request: ScopedWakeRequest) -> ExecutionView:
        task = self._submissions.get(request.wake_id)
        if task is None or task.done():
            task = asyncio.create_task(self._submit_until_bound(request))
            self._submissions[request.wake_id] = task
        else:
            record = await self.store.scoped_record(request.wake_id)
            if record is not None and (record.properties or {}).get('fingerprint') != request.fingerprint:
                raise ScopedExecutionError('submission_conflict')
        return await asyncio.shield(task)

    async def _submit_until_bound(self, request):
        while True:
            try:
                return await self._submit(request)
            except HandoffError as error:
                if not error.retryable or self._closing:
                    raise
                view = await self.status(wake_id=request.wake_id)
                record = await self.store.scoped_record(request.wake_id)
                deadline = (record.properties or {}).get('deadline_at') if record is not None else None
                if deadline is not None and datetime.fromisoformat(deadline) <= datetime.now(UTC) and not view.cancel_requested:
                    await self.cancel(request.wake_id, 'execution_timeout')
                await asyncio.sleep(self.retry_seconds)

    async def _handle_retired_bind_rejection(self, properties, error):
        # Only decoded authoritative business refusals prove bind did not commit.
        # Unknown/oversized/unreadable responses can follow a committed HTTP200;
        # retain the admission and its IDs for reconciliation in both paths.
        rejected = {'not_found', 'unauthorized', 'scope_mismatch', 'conflict',
                    'execution_closed', 'content_restricted', 'item_not_reportable'}
        if (properties.get('reactivating_retired_at') is not None and not error.retryable
                and error.code in rejected):
            await self._reject_retired_admission(properties['wake_id'])

    async def _reject_retired_admission(self, wake_id):
        # Argus explicitly rejected binding; this durable provisional execution
        # never launched a model or tool, so there is no remote binding to finalize.
        await self.store.update_scoped(wake_id, {
            'phase': 'stopped', 'execution_stopped': True,
            'exit_kind': 'start_failed', 'sync_state': 'finalized'})
        await self.store.complete_scoped_release(wake_id)

    async def _submit(self, request: ScopedWakeRequest) -> ExecutionView:
        lock = self._locks.setdefault(request.wake_id, asyncio.Lock())
        async with lock:
            identity = SessionIdentity.create(self.workspace, request.target_session_id or str(uuid4()))
            existing = await self.store.scoped_record(request.wake_id)
            retired_at = None
            if existing is not None and (existing.properties or {}).get('fingerprint') != request.fingerprint:
                raise ScopedExecutionError('submission_conflict')
            if existing is None and request.protocol_version == 2 and request.target_session_id is not None:
                target = await self.store.get_session(identity)
                properties = target.properties or {} if target is not None else {}
                if properties.get('retired_at') is not None:
                    if (target.deleted_at is not None or properties.get('kind') != 'argus_background'
                            or properties.get('owner') != 'argus'
                            or properties.get('scope') != request.scope.model_dump(mode='json')):
                        raise ScopedExecutionError('target_unavailable')
                    pending = getattr(self.client, 'session_pending', None)
                    if pending is None:
                        raise ScopedExecutionError('target_unavailable')
                    state = await pending(identity.session_id, request.scope)
                    count, related = pending_state(state, now=self.store._clock())
                    if count <= 0 or related is None:
                        raise ScopedExecutionError('target_unavailable')
                    # This permits only a hidden provisional admission. Retirement
                    # is cleared later, atomically with exact authenticated binding.
                    retired_at = properties['retired_at']
            view = await self.store.reserve_scoped(
                request, identity=identity,
                execution_id=str(uuid4()), task_id=str(uuid4()), max_active_executions=self.maximum,
                retired_at=retired_at,
            )
            record = await self.store.scoped_record(request.wake_id)
            properties = record.properties
            self._restore_version(record)
            if properties.get("launching") or view.release_complete or view.execution_stopped:
                return view
            deadline = properties.get("deadline_at")
            if deadline is None:
                deadline = (datetime.now(UTC) + timedelta(seconds=self.timeout)).isoformat()
                await self.store.update_scoped(request.wake_id, {"deadline_at": deadline})
            binding = properties.get("binding")
            if binding is None:
                await self.store.update_scoped(request.wake_id, {"binding_attempted": True})
                try:
                    binding = await self.client.bind(request, view)
                except HandoffError as error:
                    await self._handle_retired_bind_rejection(properties, error)
                    raise
                await self.store.update_scoped(request.wake_id, {"binding": binding, "bound": True})
            view = await self.status(wake_id=request.wake_id)
            if datetime.fromisoformat(deadline) <= datetime.now(UTC) and not view.cancel_requested:
                view = await self.store.update_scoped(request.wake_id, {'cancel_requested': True, 'phase': 'cancelling', 'deadline_expired': True})
            if view.cancel_requested:
                await self.store.update_scoped(request.wake_id, {
                    "phase": "stopped", "execution_stopped": True, "exit_kind": "cancelled", "sync_state": "pending_sync",
                })
                return await self._finish(request.wake_id)
            await self.store.update_scoped(request.wake_id, {"launching": True, "phase": "queued"})
            try:
                session = await self.submit_reserved(
                    record.identity, view.task_id,
                    initial_task_prompt(binding, request.model_dump(mode='json')) if request.protocol_version == 2
                    else 'Process the bound observations.', execution_id=view.execution_id,
                )
            except Exception:
                await self.store.update_scoped(request.wake_id, {'launch_error': 'execution_unknown'})
                raise ScopedExecutionError('execution_unknown', owner=await self.status(wake_id=request.wake_id)) from None
            self._sessions[request.wake_id] = session
            registered = await self.status(wake_id=request.wake_id)
            if registered.cancel_requested:
                await session.cancel_task(registered.task_id, 'cancel_during_launch')
            self._monitors[request.wake_id] = asyncio.create_task(
                self._monitor(request.wake_id, session, deadline), name="argus:execution",
            )
            return await self.status(wake_id=request.wake_id)

    async def cancel(self, wake_id: str, reason: str):
        view = await self.status(wake_id=wake_id)
        if view is None:
            raise ScopedExecutionError("not_found")
        if view.execution_stopped:
            return view
        view = await self.store.update_scoped(wake_id, {"cancel_requested": True, "phase": "cancelling"})
        session = self._sessions.get(wake_id)
        if session is not None:
            await session.cancel_task(view.task_id, reason)
        return view

    async def _monitor(self, wake_id, session, deadline):
        drain = asyncio.create_task(session.drain_execution())
        remaining = max(0, (datetime.fromisoformat(deadline) - datetime.now(UTC)).total_seconds())
        exit_kind = "completed"
        try:
            try:
                await asyncio.wait_for(asyncio.shield(drain), remaining)
            except TimeoutError:
                exit_kind = "timed_out"
                await self.cancel(wake_id, "execution_timeout")
                await asyncio.shield(drain)
            await self.flush_updates()
            view = await self.status(wake_id=wake_id)
            if self.stop_evidence is not None:
                record = await self.store.scoped_record(wake_id)
                try:
                    durable_exit = await self.stop_evidence(record.identity, view.task_id)
                except RuntimeError:
                    await self.store.update_scoped(wake_id, {'stop_error': 'persistence_unavailable'})
                    return
                if exit_kind == 'completed':
                    exit_kind = durable_exit
            elif exit_kind == 'completed' and hasattr(session, 'execution_exit_kind'):
                exit_kind = session.execution_exit_kind(view.task_id)
            if view.cancel_requested and exit_kind != "timed_out":
                exit_kind = "cancelled"
            await self.store.update_scoped(wake_id, {
                "phase": "stopped", "execution_stopped": True, "exit_kind": exit_kind, "sync_state": "pending_sync",
            })
            await self._finish_retry(wake_id)
        except HandoffError:
            # Persisted stopped/pending_sync remains owned until an explicit retry succeeds.
            return
        finally:
            if drain.done():
                self._sessions.pop(wake_id, None)
            self._monitors.pop(wake_id, None)

    async def _finish_retry(self, wake_id):
        while True:
            try:
                return await self._finish(wake_id)
            except HandoffError as error:
                if not error.retryable or self._closing:
                    return await self.status(wake_id=wake_id)
                await asyncio.sleep(self.retry_seconds)

    async def _finish(self, wake_id):
        view = await self.status(wake_id=wake_id)
        if not view.execution_stopped:
            raise ScopedExecutionError("busy", owner=view)
        record = await self.store.scoped_record(wake_id)
        self._restore_version(record)
        binding = record.properties.get("binding")
        if binding is None:
            raise ScopedExecutionError("execution_unknown", owner=view)
        await self._flush_reports(record, binding)
        view = await self.status(wake_id=wake_id)
        await self.client.finalize(wake_id, binding["binding_id"], view.exit_kind, view.report_cursor)
        await self.store.update_scoped(wake_id, {"sync_state": "finalized"})
        # Releasing this execution does not end the semantic session. Session
        # lifecycle/shutdown owns resource cleanup independently of wake IDs.
        released = await self.store.complete_scoped_release(wake_id)
        self._locks.pop(wake_id, None)
        submission = self._submissions.get(wake_id)
        if submission is not None and submission.done():
            self._submissions.pop(wake_id, None)
        return released

    async def _flush_reports(self, record, binding):
        rows = await self.store.read_internal(record.identity, update_types={
            "argus_report", "argus_report_synced", "argus_report_rejected",
        })
        rows = tuple(row for row in rows if row.payload.get("wake_id") == record.properties["wake_id"]
            and row.payload.get("binding_id") == binding["binding_id"])
        terminal = {row.payload["report_id"] for row in rows if row.update_type != "argus_report"}
        synced_cursor = max((row.payload["report_cursor"] for row in rows if row.update_type == "argus_report_synced"), default=0)
        view = await self.status(wake_id=record.properties['wake_id'])
        if synced_cursor > view.report_cursor:
            await self.store.update_scoped(view.wake_id, {'report_cursor': synced_cursor})
        for row in rows:
            if row.update_type != "argus_report" or row.payload["report_id"] in terminal:
                continue
            receipt = await self.client.send_report(record.properties["wake_id"], binding["binding_id"], {
                "report_id": row.payload["report_id"], "items": row.payload["items"],
            })
            accepted = receipt["disposition"] == "accepted"
            await self.store.append_internal(record.identity, task_id=row.task_id,
                update_type="argus_report_synced" if accepted else "argus_report_rejected", payload=receipt)
            if accepted:
                await self.store.update_scoped(record.properties["wake_id"], {"report_cursor": receipt["report_cursor"]})

    def _restore_version(self, record):
        remember = getattr(self.client, "remember_version", None)
        if remember is not None:
            remember(record.properties["wake_id"], record.properties.get("protocol_version", 1))

    async def recover(self):
        for view in await self.store.active_scoped():
            record = await self.store.scoped_record(view.wake_id)
            if not view.execution_stopped:
                view = await self.store.update_scoped(view.wake_id, {
                    "phase": "stopped", "execution_stopped": True,
                    "exit_kind": "interrupted", "sync_state": "pending_sync",
                })
            if record.properties.get("binding") is None:
                if record.properties.get("binding_attempted"):
                    request = ScopedWakeRequest.model_validate(record.properties["request"])
                    try:
                        binding = await self.client.bind(request, view)
                    except HandoffError as error:
                        await self._handle_retired_bind_rejection(record.properties, error)
                        continue
                    await self.store.update_scoped(view.wake_id, {"binding": binding, "bound": True})
                else:
                    await self.store.update_scoped(view.wake_id, {"sync_state": "finalized", "exit_kind": "start_failed"})
                    await self.store.complete_scoped_release(view.wake_id)
                    continue
            try:
                await self._finish(view.wake_id)
            except HandoffError:
                continue

    async def release(self, wake_id):
        view = await self.status(wake_id=wake_id)
        if view is None:
            raise ScopedExecutionError("not_found")
        if view.release_complete:
            return view
        return await self._finish(wake_id)

    async def drain(self):
        if self._monitors:
            await asyncio.gather(*(asyncio.shield(task) for task in tuple(self._monitors.values())))

    async def close(self):
        self._closing = True
        pending = tuple(task for task in self._submissions.values() if not task.done())
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        for wake_id in tuple(self._sessions):
            await self.cancel(wake_id, "runtime_shutdown")
        await self.drain()
