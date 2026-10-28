"""Session registry application behavior."""

from __future__ import annotations


import asyncio

from icarus.core.identity import SessionIdentity
from agent_app.application.models.status import SessionStatus
from agent_app.application.session_runtime import SessionRuntime


from agent_app.application.models.session_entry import _SessionEntry
from agent_app.application.services.common import _key, _max_datetime, _safe_error


class SessionRegistry:
    def __init__(self, *, clock, config_loader, entries, entries_lock, handle_update, publish_lifecycle, load_instructions, session_factory, logger):
        self._clock = clock
        self._config_loader = config_loader
        self._entries = entries
        self._entries_lock = entries_lock
        self._handle_update = handle_update
        self._publish_lifecycle = publish_lifecycle
        self._load_instructions = load_instructions
        self._session_factory = session_factory
        self.logger = logger

    @property
    def entries(self):
        return self._entries

    async def _entry(self, identity: SessionIdentity) -> _SessionEntry:
        key = _key(identity)
        async with self._entries_lock:
            return self._entries.setdefault(key, _SessionEntry(identity))

    def _begin_load_locked(
        self, entry: _SessionEntry
    ) -> asyncio.Task[SessionRuntime]:
        entry.lifecycle = "loading"
        entry.error = None
        task = asyncio.create_task(
            self._load_session(entry),
            name=f"session-runtime:{entry.identity.session_id}:load",
        )
        entry.load_task = task
        return task

    async def _load_session(self, entry: _SessionEntry) -> SessionRuntime:
        await self._publish_lifecycle(entry, "loading")
        runtime: SessionRuntime | None = None
        try:
            config = self._config_loader()
            instructions = await self._load_instructions(entry.identity)
            runtime = self._session_factory(
                entry.identity,
                config=config,
                publish_update=self._handle_update,
                instructions=instructions,
                logger=self.logger.getChild(entry.identity.session_id),
            )
            assert runtime is not None
            await runtime.start()
        except BaseException as error:
            if runtime is not None:
                try:
                    await runtime.stop("start_failed", timeout=5)
                except Exception:
                    self.logger.exception(
                        "SessionRuntime cleanup failed: session_id=%s",
                        entry.identity.session_id,
                    )
            async with entry.mutation_lock:
                entry.lifecycle = "failed"
                entry.error = _safe_error(error)
                if entry.load_task is asyncio.current_task():
                    entry.load_task = None
            await self._publish_lifecycle(entry, "failed")
            raise
        async with entry.mutation_lock:
            entry.runtime = runtime
            entry.lifecycle = "ready"
            entry.ready_at = self._clock()
            entry.error = None
            if entry.load_task is asyncio.current_task():
                entry.load_task = None
        await self._publish_lifecycle(entry, "ready")
        assert runtime is not None
        return runtime

    def _begin_unload_locked(
        self, entry: _SessionEntry, reason: str
    ) -> asyncio.Task[None]:
        entry.lifecycle = "unloading"
        task = asyncio.create_task(
            self._unload(entry, reason),
            name=f"session-runtime:{entry.identity.session_id}:unload",
        )
        entry.unload_task = task
        return task

    async def _unload(self, entry: _SessionEntry, reason: str) -> None:
        await self._publish_lifecycle(entry, "unloading")
        runtime = entry.runtime
        try:
            if runtime is not None:
                await runtime.stop(reason)
        except BaseException as error:
            async with entry.mutation_lock:
                entry.error = _safe_error(error)
            raise
        finally:
            async with entry.mutation_lock:
                entry.runtime = None
                entry.lifecycle = (
                    "failed" if entry.persistence_failed else "unloaded"
                )
                if entry.unload_task is asyncio.current_task():
                    entry.unload_task = None
            await self._publish_lifecycle(entry, entry.lifecycle)

    def _status(self, entry: _SessionEntry) -> SessionStatus:
        snapshot = entry.runtime.snapshot() if entry.runtime is not None else None
        lifecycle = entry.lifecycle
        if lifecycle in {"ready", "running"} and snapshot is not None:
            lifecycle = "running" if snapshot.has_work else "ready"
        activity = _max_datetime(
            entry.ready_at,
            entry.last_task_activity_at,
            snapshot.last_event_at if snapshot is not None else None,
            snapshot.last_background_work_at if snapshot is not None else None,
        )
        return SessionStatus(
            workspace_key=entry.identity.workspace_key,
            session_id=entry.identity.session_id,
            lifecycle=lifecycle,
            active_task_ids=(snapshot.active_task_ids if snapshot else ()),
            queued_task_count=(snapshot.queued_task_count if snapshot else 0),
            pending_event_count=(snapshot.pending_event_count if snapshot else 0),
            pending_plugin_event_count=(
                snapshot.pending_plugin_event_count if snapshot else 0
            ),
            background_work_count=(
                snapshot.background_work_count if snapshot else 0
            ),
            last_activity_at=activity,
            error=entry.error,
        )

