"""Session reaper application behavior."""

from __future__ import annotations


import asyncio
from pathlib import Path
from datetime import datetime

from agent_app.argus_protocol import HandoffError, Scope
from agent_app.application.services.argus_pending import pending_state

from icarus.core.identity import SessionIdentity
from agent_app.application.models.status import DiscardSessionResult, UnloadResult


from agent_app.application.services.common import _key


class SessionReaper:
    def __init__(self, *, begin_unload_locked, clock, entries, entries_lock, entry, has_active_agent_work, require_accepting, require_not_discarding, status, store, cleanup_interval, idle_timeout, logger, argus_settings=None, session_pending=None):
        self._begin_unload_locked = begin_unload_locked
        self._clock = clock
        self._entries = entries
        self._entries_lock = entries_lock
        self._entry = entry
        self._has_active_agent_work = has_active_agent_work
        self._require_accepting = require_accepting
        self._require_not_discarding = require_not_discarding
        self._status = status
        self._store = store
        self.cleanup_interval = cleanup_interval
        self.idle_timeout = idle_timeout
        self.logger = logger
        self._argus_settings = argus_settings
        self._session_pending = session_pending

    async def unload_session(
        self, workspace_path: str | Path, session_id: str
    ) -> UnloadResult:
        self._require_accepting()
        identity = SessionIdentity.create(workspace_path, session_id)
        entry = self._entries.get(_key(identity))
        if entry is None:
            return UnloadResult(identity.workspace_key, session_id, "not_found")
        while True:
            wait_for = None
            async with entry.mutation_lock:
                self._require_accepting()
                self._require_not_discarding(entry)
                if entry.load_task is not None:
                    wait_for = entry.load_task
                elif entry.unload_task is not None:
                    wait_for = entry.unload_task
                elif entry.runtime is None:
                    return UnloadResult(
                        identity.workspace_key, session_id, "already_unloaded"
                    )
                elif self._has_active_agent_work(entry):
                    return UnloadResult(identity.workspace_key, session_id, "busy")
                else:
                    wait_for = self._begin_unload_locked(
                        entry, "manual_unload"
                    )
            await asyncio.shield(wait_for)
            if entry.runtime is None:
                return UnloadResult(
                    identity.workspace_key, session_id, "unloaded"
                )

    async def discard_empty_session(
        self, workspace_path: str | Path, session_id: str
    ) -> DiscardSessionResult:
        return await self._delete_session(workspace_path, session_id, only_empty=True)

    async def delete_session(
        self, workspace_path: str | Path, session_id: str
    ) -> DiscardSessionResult:
        """Hide a conversation after stopping its idle runtime; retain stored history."""
        return await self._delete_session(workspace_path, session_id, only_empty=False)

    async def _delete_session(
        self, workspace_path: str | Path, session_id: str, *, only_empty: bool
    ) -> DiscardSessionResult:
        self._require_accepting()
        identity = SessionIdentity.create(workspace_path, session_id)
        key = _key(identity)
        entry = self._entries.get(key)
        if entry is None and not await self._store().session_exists(identity):
            return DiscardSessionResult(
                identity.workspace_key, session_id, "not_found"
            )
        entry = await self._entry(identity)
        while True:
            wait_for: asyncio.Task[None] | None = None
            async with entry.mutation_lock:
                self._require_accepting()
                if entry.discarding:
                    return DiscardSessionResult(
                        identity.workspace_key, session_id, "busy"
                    )
                if entry.load_task is not None or entry.unload_task is not None:
                    return DiscardSessionResult(
                        identity.workspace_key, session_id, "busy"
                    )
                if entry.runtime is not None and entry.runtime.snapshot().has_work:
                    return DiscardSessionResult(
                        identity.workspace_key, session_id, "busy"
                    )
                if entry.runtime is not None:
                    wait_for = self._begin_unload_locked(
                        entry, "discard_empty" if only_empty else "user_delete"
                    )
                else:
                    entry.discarding = True
                    try:
                        if only_empty:
                            status = await self._store().soft_delete_empty_session(
                                identity, reason="empty_cleanup"
                            )
                        else:
                            status = await self._store().soft_delete_session(
                                identity, reason="user_delete"
                            )
                        if status != "discarded":
                            entry.discarding = False
                            return DiscardSessionResult(
                                identity.workspace_key, session_id, status
                            )
                        async with self._entries_lock:
                            if self._entries.get(key) is entry:
                                self._entries.pop(key)
                    except BaseException:
                        entry.discarding = False
                        raise
                    return DiscardSessionResult(
                        identity.workspace_key, session_id, "discarded"
                    )
            assert wait_for is not None
            await asyncio.shield(wait_for)

    async def cleanup_idle_sessions(self) -> None:
        now = self._clock()
        unloads: list[asyncio.Task[None]] = []
        for entry in tuple(self._entries.values()):
            if entry.context.kind != 'interactive':
                continue
            status = self._status(entry)
            if status.lifecycle != "ready" or status.last_activity_at is None:
                continue
            if now - status.last_activity_at < self.idle_timeout:
                continue
            async with entry.mutation_lock:
                current = self._status(entry)
                if (
                    current.lifecycle == "ready"
                    and current.last_activity_at is not None
                    and now - current.last_activity_at >= self.idle_timeout
                    and entry.runtime is not None
                ):
                    unloads.append(
                        self._begin_unload_locked(entry, "idle_timeout")
                    )
        if unloads:
            results = await asyncio.gather(
                *(asyncio.shield(task) for task in unloads),
                return_exceptions=True,
            )
            for result in results:
                if isinstance(result, BaseException):
                    self.logger.error(
                        "Idle Session unload failed: %s", result
                    )

    async def cleanup_argus_sessions(self) -> None:
        if self._argus_settings is None or self._session_pending is None:
            return
        settings, now = self._argus_settings(), self._clock()
        for record in await self._store().argus_lifecycle_sessions():
            properties = record.properties or {}
            try:
                local = datetime.fromisoformat(properties['last_semantic_activity_at'])
                if local.tzinfo is None or local > now:
                    continue
                if (now - local).total_seconds() < settings.session_idle_seconds:
                    continue
                state = await self._session_pending(record.identity.session_id, Scope.model_validate(properties['scope']))
                count, related = pending_state(state, now=now)
            except (HandoffError, ValueError, TypeError, KeyError):
                continue
            if count:
                continue
            activity = max(local, related) if related is not None else local
            age = (now - activity).total_seconds()
            if age < settings.session_idle_seconds:
                continue
            entry = await self._entry(record.identity)
            unload = None
            async with entry.mutation_lock:
                current = await self._store().get_session(record.identity)
                if (current is None or current.scope_key is not None
                        or (current.properties or {}).get('last_semantic_activity_at') != properties['last_semantic_activity_at']
                        or entry.load_task is not None or entry.unload_task is not None or entry.persistence_failed):
                    continue
                runtime = entry.runtime
                if runtime is not None:
                    tracker = getattr(runtime, 'tool_execution_tracker', None)
                    if runtime.snapshot().has_work or (tracker is not None and tracker.pending_count):
                        continue
                rows = await self._store().read_internal(record.identity, update_types={
                    'argus_report', 'argus_report_synced', 'argus_report_rejected'})
                terminal = {(row.payload.get('wake_id'), row.payload.get('binding_id'), row.payload.get('report_id'))
                            for row in rows if row.update_type != 'argus_report'}
                if any((row.payload.get('wake_id'), row.payload.get('binding_id'), row.payload.get('report_id')) not in terminal
                       for row in rows if row.update_type == 'argus_report'):
                    continue
                if age >= settings.session_retention_seconds:
                    if not await self._store().retire_argus_session(record.identity,
                            expected_activity=properties['last_semantic_activity_at']):
                        continue
                if runtime is not None:
                    unload = self._begin_unload_locked(entry, 'argus_soft_idle')
            if unload is not None:
                await asyncio.shield(unload)

    async def _cleanup_loop(self) -> None:
        while True:
            try:
                interval = self.cleanup_interval.total_seconds()
                if self._argus_settings is not None:
                    interval = min(interval, self._argus_settings().session_idle_seconds)
                await asyncio.sleep(interval)
                await self.cleanup_idle_sessions()
                await self.cleanup_argus_sessions()
            except asyncio.CancelledError:
                raise
            except Exception:
                self.logger.exception("Idle Session cleanup failed")

