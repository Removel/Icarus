"""Session reaper application behavior."""

from __future__ import annotations


import asyncio
from pathlib import Path

from icarus.core.identity import SessionIdentity
from agent_app.application.models.status import DiscardSessionResult, UnloadResult


from agent_app.application.services.common import _key


class SessionReaper:
    def __init__(self, *, begin_unload_locked, clock, entries, entries_lock, entry, has_active_agent_work, require_accepting, require_not_discarding, status, store, cleanup_interval, idle_timeout, logger):
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

    async def _cleanup_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(self.cleanup_interval.total_seconds())
                await self.cleanup_idle_sessions()
            except asyncio.CancelledError:
                raise
            except Exception:
                self.logger.exception("Idle Session cleanup failed")

