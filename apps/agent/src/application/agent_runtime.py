"""Device-level manager for multiple SessionRuntime instances."""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from hashlib import sha256
import json
import logging
from pathlib import Path
from uuid import uuid4

from apps.agent.src.agent_orchestration.plugins.persistence import (
    DataPathResolver,
    SessionIdentity,
)
from apps.agent.src.agent_orchestration.plugins.user_input import InputAccepted
from apps.agent.src.agent_orchestration.run_control import TaskOperationResult
from apps.agent.src.application.resource_ref import ResourceRef
from apps.agent.src.application.runtime_status import (
    DiscardSessionResult,
    SessionLifecycle,
    SessionStatus,
    SessionSummary,
    TaskStatus,
    UnloadResult,
)
from apps.agent.src.application.runtime_update_stream import (
    RuntimeUpdateStream,
    RuntimeUpdateSubscription,
)
from apps.agent.src.application.session_runtime import SessionRuntime
from apps.agent.src.application.session_store import (
    SessionAlreadyExistsError,
    SessionNotFoundError,
    SessionStore,
)
from apps.agent.src.model_config import ConfigModel, get_config
from apps.agent.src.runtime_update import RuntimeUpdate


class SubmissionConflictError(ValueError):
    pass


class AgentRuntimeStoppingError(RuntimeError):
    pass


@dataclass(frozen=True)
class _SubmissionRecord:
    fingerprint: str
    accepted: InputAccepted


@dataclass
class _SessionEntry:
    identity: SessionIdentity
    mutation_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    runtime: SessionRuntime | None = None
    load_task: asyncio.Task[SessionRuntime] | None = None
    unload_task: asyncio.Task[None] | None = None
    discarding: bool = False
    lifecycle: SessionLifecycle = "unloaded"
    ready_at: datetime | None = None
    last_task_activity_at: datetime | None = None
    error: str | None = None
    persistence_failed: bool = False
    submissions: OrderedDict[str, _SubmissionRecord] = field(
        default_factory=OrderedDict
    )
    tasks: OrderedDict[str, TaskStatus] = field(default_factory=OrderedDict)


class AgentRuntime:
    def __init__(
        self,
        *,
        config_loader: Callable[[], ConfigModel] = get_config,
        idle_timeout: timedelta = timedelta(hours=6),
        cleanup_interval: timedelta = timedelta(hours=2),
        update_queue_capacity: int = 4096,
        submission_history_limit: int = 1024,
        task_history_limit: int = 1024,
        data_dir: str | Path | None = None,
        clock: Callable[[], datetime] | None = None,
        session_factory=SessionRuntime,
        session_store: SessionStore | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        if idle_timeout.total_seconds() <= 0:
            raise ValueError("idle_timeout must be positive")
        if cleanup_interval.total_seconds() <= 0:
            raise ValueError("cleanup_interval must be positive")
        if submission_history_limit < 1 or task_history_limit < 1:
            raise ValueError("history limits must be positive")
        self._config_loader = config_loader
        self.idle_timeout = idle_timeout
        self.cleanup_interval = cleanup_interval
        self.submission_history_limit = submission_history_limit
        self.task_history_limit = task_history_limit
        self._clock = clock or (lambda: datetime.now(UTC))
        self._session_factory = session_factory
        self._session_store = session_store
        self._data_dir = (
            Path(data_dir).expanduser().resolve()
            if data_dir is not None
            else None
        )
        self.logger = logger or logging.getLogger("icarus.agent.runtime")
        self._entries: dict[tuple[str, str], _SessionEntry] = {}
        self._entries_lock = asyncio.Lock()
        self._path_resolver: DataPathResolver | None = None
        self._updates = RuntimeUpdateStream(update_queue_capacity)
        self._update_queue: asyncio.Queue[RuntimeUpdate] = asyncio.Queue()
        self._update_task: asyncio.Task[None] | None = None
        self._cleanup_task: asyncio.Task[None] | None = None
        self._started = False
        self._stopping = False
        self._closed = False

    @property
    def is_running(self) -> bool:
        return self._started and not self._stopping

    async def start(self) -> None:
        if self._started:
            return
        if self._closed:
            raise RuntimeError("AgentRuntime cannot be restarted")
        if self._data_dir is None:
            config = self._config_loader()
            if config.icarus_data_dir is None:
                raise RuntimeError("ICARUS_DATA_DIR is required")
            self._data_dir = config.icarus_data_dir.expanduser().resolve()
        self._path_resolver = DataPathResolver(self._data_dir)
        if self._session_store is None:
            self._session_store = SessionStore(self._data_dir)
        await self._session_store.start()
        self._started = True
        self._stopping = False
        self._update_task = asyncio.create_task(
            self._update_loop(), name="agent-runtime:updates"
        )
        self._cleanup_task = asyncio.create_task(
            self._cleanup_loop(), name="agent-runtime:idle-cleanup"
        )

    def subscribe_updates(self) -> RuntimeUpdateSubscription:
        return self._updates.subscribe()

    async def create_session(
        self,
        workspace_path: str | Path,
        session_id: str | None = None,
    ) -> str:
        self._require_accepting()
        identity = SessionIdentity.create(workspace_path, session_id or uuid4().hex)
        if self._entries.get(_key(identity)) is None and await self._store().session_exists(
            identity, include_deleted=True
        ):
            raise SessionAlreadyExistsError(identity.session_id)
        entry = await self._entry(identity)
        async with entry.mutation_lock:
            self._require_accepting()
            self._require_not_discarding(entry)
            exists = await self._store().session_exists(
                identity, include_deleted=True
            )
            if exists or entry.lifecycle in {
                "loading", "ready", "running", "unloading"
            }:
                raise SessionAlreadyExistsError(identity.session_id)
            await self._store().create_session(identity)
            task = self._begin_load_locked(entry)
        await asyncio.shield(task)
        return identity.session_id

    async def submit(
        self,
        workspace_path: str | Path,
        session_id: str,
        prompt: str,
        *,
        submission_id: str,
        resources: tuple[ResourceRef, ...] = (),
        display_text: str | None = None,
    ) -> InputAccepted:
        self._require_accepting()
        if not submission_id.strip():
            raise ValueError("submission_id cannot be empty")
        identity = SessionIdentity.create(workspace_path, session_id)
        entry = await self._entry(identity)
        fingerprint = _submission_fingerprint(
            prompt, resources, display_text
        )
        while True:
            wait_for: asyncio.Task | None = None
            async with entry.mutation_lock:
                self._require_accepting()
                self._require_not_discarding(entry)
                self._require_persistence_available(entry)
                record = entry.submissions.get(submission_id)
                if record is not None:
                    if record.fingerprint != fingerprint:
                        raise SubmissionConflictError(submission_id)
                    return record.accepted
                if entry.unload_task is not None:
                    wait_for = entry.unload_task
                elif entry.runtime is None:
                    if entry.load_task is None:
                        if not await self._store().session_exists(identity):
                            raise SessionNotFoundError(identity.session_id)
                        entry.load_task = self._begin_load_locked(entry)
                    wait_for = entry.load_task
                else:
                    images = await self._import_resources(
                        entry.runtime, resources
                    )
                    task_id = uuid4().hex
                    accepted = await entry.runtime.submit(
                        prompt,
                        images,
                        task_id=task_id,
                    )
                    now = self._clock()
                    entry.last_task_activity_at = now
                    self._remember_task(
                        entry,
                        TaskStatus(
                            workspace_key=identity.workspace_key,
                            session_id=identity.session_id,
                            task_id=accepted.task_id,
                            lifecycle="accepted",
                            queue_position=accepted.queue_position,
                            updated_at=now,
                        ),
                    )
                    try:
                        user_update = await self._store().append_update(
                            identity,
                            RuntimeUpdate(
                                workspace_key=identity.workspace_key,
                                session_id=identity.session_id,
                                task_id=accepted.task_id,
                                type="user.message",
                                payload={
                                    "text": (
                                        prompt
                                        if display_text is None
                                        else display_text
                                    ),
                                    "resources": [
                                        {
                                            "resource_id": image.source,
                                            "media_type": image.media_type,
                                        }
                                        for image in (images or [])
                                    ],
                                },
                                occurred_at=now,
                            ),
                        )
                    except BaseException as error:
                        await entry.runtime.cancel_task(
                            accepted.task_id, "persistence_failed"
                        )
                        entry.persistence_failed = True
                        entry.lifecycle = "failed"
                        entry.error = _safe_error(error)
                        await self._updates.publish(
                            self._lifecycle_update(entry, "failed")
                        )
                        if entry.unload_task is None:
                            self._begin_unload_locked(
                                entry, "persistence_failed"
                            )
                        raise
                    self._remember_submission(
                        entry, submission_id, fingerprint, accepted
                    )
                    await self._updates.publish(user_update)
                    return accepted
            assert wait_for is not None
            await asyncio.shield(wait_for)

    async def cancel_task(
        self,
        workspace_path: str | Path,
        session_id: str,
        task_id: str,
        reason: str | None = None,
    ) -> TaskOperationResult:
        self._require_accepting()
        identity = SessionIdentity.create(workspace_path, session_id)
        entry = self._entries.get(_key(identity))
        if entry is None:
            return TaskOperationResult(task_id=task_id, status="not_running")
        async with entry.mutation_lock:
            self._require_accepting()
            if entry.discarding:
                return TaskOperationResult(
                    task_id=task_id, status="not_running"
                )
            if entry.runtime is None or entry.lifecycle in {"loading", "unloading"}:
                return TaskOperationResult(task_id=task_id, status="not_running")
            result = await entry.runtime.cancel_task(task_id, reason)
            if result.status in {"accepted", "already_cancelling"}:
                entry.last_task_activity_at = self._clock()
            return result

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
                elif entry.runtime.snapshot().has_work:
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

    async def get_session_status(
        self, workspace_path: str | Path, session_id: str
    ) -> SessionStatus:
        identity = SessionIdentity.create(workspace_path, session_id)
        entry = self._entries.get(_key(identity))
        if entry is None:
            if not await self._store().session_exists(identity):
                raise SessionNotFoundError(session_id)
            return SessionStatus(identity.workspace_key, session_id, "unloaded")
        self._require_not_discarding(entry)
        return self._status(entry)

    async def list_session_statuses(
        self, workspace_path: str | Path
    ) -> tuple[SessionStatus, ...]:
        workspace_identity = SessionIdentity.create(workspace_path, "workspace")
        session_ids = set(
            await self._store().list_session_ids(workspace_identity.workspace_key)
        )
        session_ids.update(
            entry.identity.session_id
            for entry in self._entries.values()
            if entry.identity.workspace_key == workspace_identity.workspace_key
        )
        return tuple(
            await self.get_session_status(workspace_path, session_id)
            for session_id in sorted(session_ids)
        )

    async def list_session_summaries(
        self, workspace_path: str | Path
    ) -> tuple[SessionSummary, ...]:
        workspace_identity = SessionIdentity.create(workspace_path, "workspace")
        return await self._store().list_session_summaries(
            workspace_identity.workspace_key
        )

    async def discard_empty_session(
        self, workspace_path: str | Path, session_id: str
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
                        entry, "discard_empty"
                    )
                else:
                    entry.discarding = True
                    try:
                        status = await self._store().soft_delete_empty_session(
                            identity, reason="empty_cleanup"
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

    def get_task_status(
        self, workspace_path: str | Path, session_id: str, task_id: str
    ) -> TaskStatus:
        identity = SessionIdentity.create(workspace_path, session_id)
        entry = self._entries.get(_key(identity))
        if entry is None or task_id not in entry.tasks:
            raise KeyError("Task status is unavailable")
        return entry.tasks[task_id]

    async def get_session_history(
        self,
        workspace_path: str | Path,
        session_id: str,
        *,
        after_sequence: int = 0,
    ) -> tuple[tuple[RuntimeUpdate, ...], int]:
        self._require_accepting()
        identity = SessionIdentity.create(workspace_path, session_id)
        if not await self._store().session_exists(identity):
            raise SessionNotFoundError(session_id)
        entry = await self._entry(identity)
        async with entry.mutation_lock:
            self._require_not_discarding(entry)
            store = self._store()
            all_records, cursor = await store.read_updates(identity)
            terminal_task_ids = {
                item.task_id
                for item in all_records
                if item.type == "task.finished" and item.task_id is not None
            }
            seen_task_ids = {
                item.task_id
                for item in all_records
                if item.task_id is not None
            }
            runtime_snapshot = (
                entry.runtime.snapshot()
                if entry.runtime is not None
                else None
            )
            active_task_ids = (
                set(runtime_snapshot.active_task_ids)
                if runtime_snapshot is not None
                else set()
            )
            active_task_ids.update(
                task_id
                for task_id, status in entry.tasks.items()
                if status.lifecycle in {"accepted", "running"}
            )
            interrupted_task_ids = (
                seen_task_ids - terminal_task_ids - active_task_ids
            )
            for task_id in sorted(interrupted_task_ids):
                interrupted = await store.append_update(
                    identity,
                    RuntimeUpdate(
                        workspace_key=identity.workspace_key,
                        session_id=identity.session_id,
                        task_id=task_id,
                        type="task.finished",
                        payload={
                            "status": "interrupted",
                            "run_id": None,
                            "recovered": True,
                        },
                        occurred_at=self._clock(),
                    ),
                )
                all_records = (*all_records, interrupted)
                cursor = interrupted.sequence or cursor
                await self._updates.publish(interrupted)
            return (
                tuple(
                    item
                    for item in all_records
                    if item.sequence is not None
                    and item.sequence > after_sequence
                ),
                cursor,
            )

    async def stop(self) -> None:
        if not self._started:
            return
        self._stopping = True
        cleanup = self._cleanup_task
        self._cleanup_task = None
        if cleanup is not None:
            cleanup.cancel()
            await asyncio.gather(cleanup, return_exceptions=True)
        entries = tuple(self._entries.values())
        load_tasks = tuple(
            entry.load_task for entry in entries if entry.load_task is not None
        )
        if load_tasks:
            for task in load_tasks:
                task.cancel()
            await asyncio.gather(
                *(asyncio.shield(task) for task in load_tasks),
                return_exceptions=True,
            )
        tasks = []
        for entry in entries:
            async with entry.mutation_lock:
                if entry.runtime is not None and entry.unload_task is None:
                    tasks.append(
                        self._begin_unload_locked(entry, "runtime_shutdown")
                    )
                elif entry.unload_task is not None:
                    tasks.append(entry.unload_task)
        errors = []
        if tasks:
            results = await asyncio.gather(
                *(asyncio.shield(task) for task in tasks),
                return_exceptions=True,
            )
            errors.extend(
                result for result in results if isinstance(result, BaseException)
            )
        await self._update_queue.join()
        update_task = self._update_task
        self._update_task = None
        if update_task is not None:
            update_task.cancel()
            await asyncio.gather(update_task, return_exceptions=True)
        self._updates.close()
        store = self._session_store
        if store is not None:
            try:
                await store.close()
            except BaseException as error:
                errors.append(error)
        self._started = False
        self._closed = True
        if errors:
            raise RuntimeError(
                "AgentRuntime shutdown failed: "
                + "; ".join(str(error) for error in errors)
            )

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
            runtime = self._session_factory(
                entry.identity,
                config=config,
                publish_update=self._handle_update,
                logger=self.logger.getChild(entry.identity.session_id),
            )
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

    async def _handle_update(self, update: RuntimeUpdate) -> None:
        await self._update_queue.put(update)

    async def _update_loop(self) -> None:
        while True:
            update = await self._update_queue.get()
            try:
                entry = self._entries.get(
                    (update.workspace_key, update.session_id)
                )
                if (
                    entry is not None
                    and entry.persistence_failed
                    and update.type != "session.lifecycle"
                ):
                    self.logger.warning(
                        "Ignoring RuntimeUpdate after persistence failure: "
                        "session_id=%s type=%s",
                        entry.identity.session_id,
                        update.type,
                    )
                    continue
                lifecycle_update: SessionLifecycle | None = None
                recorded_update = update
                checkpoint_error_update: RuntimeUpdate | None = None
                if entry is not None:
                    async with entry.mutation_lock:
                        previous = entry.lifecycle
                        self._apply_task_update(entry, update)
                        if (
                            update.type == "task.finished"
                            and entry.runtime is not None
                            and entry.lifecycle in {"ready", "running"}
                        ):
                            try:
                                errors = await entry.runtime.checkpoint()
                            except Exception as error:
                                self.logger.exception(
                                    "Session checkpoint failed: session_id=%s",
                                    entry.identity.session_id,
                                )
                                errors = (type(error).__name__,)
                            if errors:
                                checkpoint_error_update = RuntimeUpdate(
                                    workspace_key=update.workspace_key,
                                    session_id=update.session_id,
                                    task_id=update.task_id,
                                    type="task.error",
                                    payload={
                                        "fatal": True,
                                        "code": "state_checkpoint_failed",
                                        "error_type": "PersistenceError",
                                        "message": (
                                            "Session state could not be saved"
                                        ),
                                        "step": None,
                                        "run_id": update.payload.get("run_id"),
                                    },
                                    occurred_at=update.occurred_at,
                                )
                                self._apply_task_update(
                                    entry, checkpoint_error_update
                                )
                                update = RuntimeUpdate(
                                    workspace_key=update.workspace_key,
                                    session_id=update.session_id,
                                    task_id=update.task_id,
                                    type="task.finished",
                                    payload={
                                        "status": "failed",
                                        "run_id": update.payload.get("run_id"),
                                        "error_code": "state_checkpoint_failed",
                                    },
                                    occurred_at=update.occurred_at,
                                )
                                self._apply_task_update(entry, update)
                        if (
                            entry.runtime is not None
                            and previous in {"ready", "running"}
                            and update.type in {
                                "task.accepted",
                                "task.started",
                                "task.finished",
                            }
                        ):
                            projected = "running"
                            if update.type == "task.finished":
                                projected = (
                                    "running"
                                    if self._has_nonterminal_tasks(entry)
                                    or entry.runtime.snapshot().background_work_count
                                    else "ready"
                                )
                            entry.lifecycle = projected
                            if projected != previous:
                                lifecycle_update = projected
                        if update.type != "session.lifecycle":
                            if update.type == "task.accepted":
                                accepted = entry.tasks.get(update.task_id or "")
                                if accepted is None:
                                    raise RuntimeError(
                                        "Task accepted Update arrived before submit commit"
                                    )
                            if checkpoint_error_update is not None:
                                recorded_error = await self._store().append_update(
                                    entry.identity, checkpoint_error_update
                                )
                                await self._updates.publish(recorded_error)
                            recorded_update = await self._store().append_update(
                                entry.identity, update
                            )
                elif update.type != "session.lifecycle":
                    raise RuntimeError(
                        "RuntimeUpdate belongs to an unknown Session"
                    )
                await self._updates.publish(recorded_update)
                if entry is not None and lifecycle_update is not None:
                    await self._updates.publish(
                        self._lifecycle_update(entry, lifecycle_update)
                    )
            except Exception as error:
                self.logger.exception("RuntimeUpdate processing failed")
                if entry is not None:
                    async with entry.mutation_lock:
                        entry.persistence_failed = True
                        entry.lifecycle = "failed"
                        entry.error = _safe_error(error)
                        runtime = entry.runtime
                        task_ids = tuple(
                            status.task_id
                            for status in entry.tasks.values()
                            if status.lifecycle in {"accepted", "running"}
                        )
                    if runtime is not None:
                        for task_id in task_ids:
                            try:
                                await runtime.cancel_task(
                                    task_id, "persistence_failed"
                                )
                            except Exception:
                                self.logger.exception(
                                    "Task cancellation after persistence failure failed"
                                )
                    await self._updates.publish(
                        self._lifecycle_update(entry, "failed")
                    )
                    async with entry.mutation_lock:
                        if entry.runtime is not None and entry.unload_task is None:
                            self._begin_unload_locked(
                                entry, "persistence_failed"
                            )
            finally:
                self._update_queue.task_done()

    def _apply_task_update(
        self, entry: _SessionEntry, update: RuntimeUpdate
    ) -> None:
        task_id = update.task_id
        if task_id is None:
            return
        current = entry.tasks.get(task_id)
        lifecycle = current.lifecycle if current is not None else "accepted"
        run_id = current.run_id if current is not None else None
        error_code = current.error_code if current is not None else None
        queue_position = (
            current.queue_position if current is not None else None
        )
        if update.type == "task.accepted":
            lifecycle = "accepted"
            queue_position = int(update.payload["queue_position"])
        elif update.type == "task.started":
            lifecycle = "running"
        elif update.type == "task.error":
            error_code = str(update.payload["code"])
            if bool(update.payload["fatal"]):
                lifecycle = "failed"
        elif update.type == "task.finished":
            lifecycle = str(update.payload["status"])  # type: ignore[assignment]
            raw_run_id = update.payload.get("run_id")
            run_id = str(raw_run_id) if raw_run_id is not None else None
            raw_error_code = update.payload.get("error_code")
            if raw_error_code is not None:
                error_code = str(raw_error_code)
        entry.last_task_activity_at = self._clock()
        self._remember_task(
            entry,
            TaskStatus(
                workspace_key=entry.identity.workspace_key,
                session_id=entry.identity.session_id,
                task_id=task_id,
                lifecycle=lifecycle,
                queue_position=queue_position,
                run_id=run_id,
                error_code=error_code,
                updated_at=self._clock(),
            ),
        )

    async def _publish_lifecycle(
        self, entry: _SessionEntry, lifecycle: SessionLifecycle
    ) -> None:
        await self._handle_update(self._lifecycle_update(entry, lifecycle))

    def _lifecycle_update(
        self, entry: _SessionEntry, lifecycle: SessionLifecycle
    ) -> RuntimeUpdate:
        return RuntimeUpdate(
            workspace_key=entry.identity.workspace_key,
            session_id=entry.identity.session_id,
            task_id=None,
            type="session.lifecycle",
            payload={"status": lifecycle, "error": entry.error},
            occurred_at=self._clock(),
        )

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

    def _store(self) -> SessionStore:
        if self._session_store is None:
            raise RuntimeError("AgentRuntime is not started")
        return self._session_store

    async def _import_resources(
        self, runtime: SessionRuntime, resources: tuple[ResourceRef, ...]
    ):
        if not resources:
            return None
        resolver = self._path_resolver
        if resolver is None:
            raise RuntimeError("AgentRuntime is not started")
        paths = [
            (resource.resolve(resolver.incoming_dir), resource.media_type)
            for resource in resources
        ]
        return await asyncio.to_thread(runtime.import_resources, paths)

    def _remember_submission(
        self,
        entry: _SessionEntry,
        submission_id: str,
        fingerprint: str,
        accepted: InputAccepted,
    ) -> None:
        entry.submissions[submission_id] = _SubmissionRecord(
            fingerprint, accepted
        )
        entry.submissions.move_to_end(submission_id)
        while len(entry.submissions) > self.submission_history_limit:
            entry.submissions.popitem(last=False)

    def _remember_task(self, entry: _SessionEntry, status: TaskStatus) -> None:
        entry.tasks[status.task_id] = status
        entry.tasks.move_to_end(status.task_id)
        while len(entry.tasks) > self.task_history_limit:
            entry.tasks.popitem(last=False)

    @staticmethod
    def _has_nonterminal_tasks(entry: _SessionEntry) -> bool:
        return any(
            status.lifecycle in {"accepted", "running"}
            for status in entry.tasks.values()
        )

    def _require_accepting(self) -> None:
        if not self.is_running:
            raise AgentRuntimeStoppingError("AgentRuntime is not accepting calls")

    @staticmethod
    def _require_not_discarding(entry: _SessionEntry) -> None:
        if entry.discarding:
            raise SessionNotFoundError(entry.identity.session_id)

    @staticmethod
    def _require_persistence_available(entry: _SessionEntry) -> None:
        if entry.persistence_failed:
            raise RuntimeError("Session persistence is unavailable")


def _key(identity: SessionIdentity) -> tuple[str, str]:
    return identity.workspace_key, identity.session_id


def _submission_fingerprint(
    prompt: str,
    resources: tuple[ResourceRef, ...],
    display_text: str | None,
) -> str:
    payload = {
        "prompt": prompt,
        "display_text": display_text,
        "resources": [
            {
                "resource_id": item.resource_id,
                "media_type": item.media_type,
            }
            for item in resources
        ],
    }
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def _max_datetime(*values: datetime | None) -> datetime | None:
    present = tuple(value for value in values if value is not None)
    return max(present) if present else None


def _safe_error(error: BaseException) -> str:
    return type(error).__name__
