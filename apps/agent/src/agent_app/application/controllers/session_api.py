"""Device-level manager for multiple SessionRuntime instances."""

from __future__ import annotations

from icarus.runtime.fanout import DEFAULT_QUEUE_CAPACITY

from agent_app.contracts.prompt import PromptError
from agent_app.plugins.prompt.composer import validate_instructions
from agent_app.plugins.prompt.config import PluginConfig as PromptConfig
from agent_app.application.errors import SubmissionConflictError, AgentRuntimeStoppingError

import asyncio
from typing import Any, cast
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
import logging
from pathlib import Path
from uuid import uuid4

from icarus.runtime.paths import DataPathResolver
from icarus.core.identity import SessionIdentity
from icarus.core.contracts.input import InputAccepted
from icarus.core.run_control import TaskOperationResult
from icarus.core.resource_ref import ResourceRef
from agent_app.application.models.status import (
    DiscardSessionResult,
    SessionLifecycle,
    SessionStatus,
    SessionSummary,
    TaskStatus,
    UnloadResult,
)
from agent_app.application.runtime_update_stream import (
    RuntimeUpdateStream,
    RuntimeUpdateSubscription,
)
from agent_app.application.session_runtime import SessionRuntime
from agent_app.application.session_title import generate_title
from agent_app.application.repositories.session_store import (
    SessionAlreadyExistsError,
    SessionNotFoundError,
    SessionStore,
)
from agent_app.config import ConfigModel, get_config
from icarus.core.runtime_update import RuntimeUpdate


TRANSIENT_UPDATE_TYPES = frozenset(
    {
        "session.lifecycle",
        "assistant.text_delta",
        "assistant.thinking_delta",
    }
)


from agent_app.application.models.session_entry import _SessionEntry
from agent_app.application.services.common import _key, _submission_fingerprint, _steer_fingerprint, _safe_error

from agent_app.application.services.session_registry import SessionRegistry
from agent_app.application.services.submission_guard import SubmissionGuard
from agent_app.application.services.resource_importer import ResourceImporter
from agent_app.application.services.update_pipeline import UpdatePipeline
from agent_app.application.services.history_projector import HistoryProjector
from agent_app.application.services.session_reaper import SessionReaper

class SessionApi:
    def __init__(
        self,
        *,
        config_loader: Callable[[], ConfigModel] = get_config,
        idle_timeout: timedelta = timedelta(hours=6),
        cleanup_interval: timedelta = timedelta(hours=2),
        update_queue_capacity: int | None = None,
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
        self._capacity_override = update_queue_capacity
        self._updates = RuntimeUpdateStream(update_queue_capacity or DEFAULT_QUEUE_CAPACITY)
        self._update_queue: asyncio.Queue[RuntimeUpdate] = asyncio.Queue()
        self._update_task: asyncio.Task[None] | None = None
        self._cleanup_task: asyncio.Task[None] | None = None
        self._started = False
        self._stopping = False
        self._closed = False
        self.sessions = SessionRegistry(clock=self._clock, config_loader=self._config_loader, entries=self._entries, entries_lock=self._entries_lock, handle_update=self._handle_update, publish_lifecycle=self._publish_lifecycle, load_instructions=lambda identity: self._store().get_session_instructions(identity), session_factory=lambda *args, **kwargs: self._session_factory(*args, **kwargs), logger=self.logger)
        self.submissions = SubmissionGuard(submission_history_limit=self.submission_history_limit, task_history_limit=self.task_history_limit)
        self.resources = ResourceImporter(path_resolver_getter=lambda: self._path_resolver)
        self.updates = UpdatePipeline(begin_unload_locked=self._begin_unload_locked, clock=self._clock, entries=self._entries, has_nonterminal_tasks=self._has_nonterminal_tasks, lifecycle_update=self._lifecycle_update, remember_task=self._remember_task, store=self._store, update_queue=self._update_queue, updates=self._updates, logger=self.logger)
        self.history = HistoryProjector(clock=self._clock, entry=self._entry, require_accepting=self._require_accepting, require_not_discarding=self._require_not_discarding, store=self._store, updates=self._updates)
        self.reaper = SessionReaper(begin_unload_locked=self._begin_unload_locked, clock=self._clock, entries=self._entries, entries_lock=self._entries_lock, entry=self._entry, has_active_agent_work=self._has_active_agent_work, require_accepting=self._require_accepting, require_not_discarding=self._require_not_discarding, status=self._status, store=self._store, cleanup_interval=self.cleanup_interval, idle_timeout=self.idle_timeout, logger=self.logger)

    @property
    def update_queue_capacity(self) -> int:
        return self._updates.capacity

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
        if self._capacity_override is None:
            self._updates.configure_capacity(self._config_loader().runtime.update_queue_capacity)
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

    def get_memory_context(self, workspace_path: str | Path | None = None) -> dict:
        """Return the configured memory identity without loading a session or exposing secrets."""
        config = self._config_loader()
        memory = config.runtime.plugin_config.get("memory", {})
        user_id = memory.get("user_id")
        agent_id = memory.get("agent_id")
        if not all(isinstance(value, str) and value.strip() for value in (user_id, agent_id)):
            raise ValueError("Memory user_id and agent_id must be configured")
        result = {"user_id": cast(str, user_id).strip(), "agent_id": cast(str, agent_id).strip(), "run_id": "global"}
        if workspace_path is not None:
            path = Path(workspace_path).expanduser()
            if not path.is_absolute():
                raise ValueError("Workspace path must be absolute")
            identity = SessionIdentity.create(path)
            result.update(workspace_path=str(identity.workspace_path),
                          run_id=f"workspace:{identity.workspace_key}")
        return result

    def validate_session_instructions(self, instructions: str | None) -> None:
        config = PromptConfig.model_validate(self._config_loader().runtime.plugin_config.get("prompt", {}))
        validate_instructions(instructions, max_tokens=config.max_section_tokens)

    async def create_session(
        self,
        workspace_path: str | Path,
        session_id: str | None = None,
        *,
        load_runtime: bool = True,
        instructions: str | None = None,
    ) -> str:
        self._require_accepting()
        self.validate_session_instructions(instructions)
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
            await self._store().create_session(identity, instructions=instructions)
            if not load_runtime:
                return identity.session_id
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

    async def steer_task(
        self,
        workspace_path: str | Path,
        session_id: str,
        task_id: str,
        content: str,
        *,
        resources: tuple[ResourceRef, ...] = (),
        display_text: str | None = None,
        submission_id: str | None = None,
    ) -> TaskOperationResult:
        self._require_accepting()
        if submission_id is not None and not submission_id.strip():
            raise ValueError("submission_id cannot be empty")
        identity = SessionIdentity.create(workspace_path, session_id)
        entry = self._entries.get(_key(identity))
        if entry is None:
            return TaskOperationResult(task_id=task_id, status="not_running")
        fingerprint = _steer_fingerprint(
            task_id, content, resources, display_text
        )
        async with entry.mutation_lock:
            self._require_accepting()
            if entry.discarding:
                return TaskOperationResult(task_id=task_id, status="not_running")
            if entry.runtime is None or entry.lifecycle in {"loading", "unloading"}:
                return TaskOperationResult(task_id=task_id, status="not_running")
            if submission_id is not None:
                record = entry.steer_submissions.get(submission_id)
                if record is not None:
                    if record.fingerprint != fingerprint:
                        raise SubmissionConflictError(
                            "submission_id was already used with different content"
                        )
                    entry.steer_submissions.move_to_end(submission_id)
                    return record.result
            images = await self._import_resources(entry.runtime, resources)
            steer_kwargs: dict[str, Any] = {
                "display_text": display_text
            }
            if submission_id is not None:
                steer_kwargs["submission_id"] = submission_id
            result = await entry.runtime.steer_task(
                task_id, content, images, **steer_kwargs
            )
            if result.status == "accepted":
                entry.last_task_activity_at = self._clock()
                if submission_id is not None:
                    self._remember_steer_submission(
                        entry, submission_id, fingerprint, result
                    )
            return result

    async def unload_session(
        self, workspace_path: str | Path, session_id: str
    ) -> UnloadResult:
        return await self.reaper.unload_session(workspace_path, session_id)

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
        return tuple([
            await self.get_session_status(workspace_path, session_id)
            for session_id in sorted(session_ids)
        ])

    async def list_session_summaries(
        self, workspace_path: str | Path
    ) -> tuple[SessionSummary, ...]:
        workspace_identity = SessionIdentity.create(workspace_path, "workspace")
        return await self._store().list_session_summaries(
            workspace_identity.workspace_key
        )

    async def generate_session_title(
        self, workspace_path: str | Path, session_id: str
    ) -> str | None:
        self._require_accepting()
        identity = SessionIdentity.create(workspace_path, session_id)
        entry = await self._entry(identity)
        async with entry.title_lock:
            store = self._store()
            record = await store.get_session(identity)
            if record is None:
                raise SessionNotFoundError(session_id)
            title = await store.get_title(identity)
            if title is not None or record.first_user_input is None:
                return title
            records, _ = await store.read_updates(identity)
            first = next(item for item in records if item.type == "user.message")
            text = first.payload.get("text")
            if not isinstance(text, str) or not text.strip():
                return None
            try:
                title = await generate_title(self._config_loader(), text)
            except Exception:
                self.logger.warning(
                    "Session title generation unavailable", exc_info=True
                )
                return None
            self._require_accepting()
            return await store.save_title(identity, title)

    async def discard_empty_session(
        self, workspace_path: str | Path, session_id: str
    ) -> DiscardSessionResult:
        return await self.reaper.discard_empty_session(workspace_path, session_id)

    async def delete_session(
        self, workspace_path: str | Path, session_id: str
    ) -> DiscardSessionResult:
        return await self.reaper.delete_session(workspace_path, session_id)

    async def _delete_session(
        self, workspace_path: str | Path, session_id: str, *, only_empty: bool
    ) -> DiscardSessionResult:
        return await self.reaper._delete_session(workspace_path, session_id, only_empty=only_empty)

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
        return await self.history.get_session_history(workspace_path, session_id, after_sequence=after_sequence)

    async def get_session_history_page(self, workspace_path: str | Path, session_id: str, *,
                                       after_sequence: int = 0, limit: int = 200):
        return await self.history.page(workspace_path, session_id, after_sequence=after_sequence, limit=limit)

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
        errors: list[BaseException] = []
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
        return await self.reaper.cleanup_idle_sessions()

    async def _cleanup_loop(self) -> None:
        return await self.reaper._cleanup_loop()

    async def _entry(self, identity: SessionIdentity) -> _SessionEntry:
        return await self.sessions._entry(identity)

    def _begin_load_locked(
        self, entry: _SessionEntry
    ) -> asyncio.Task[SessionRuntime]:
        return self.sessions._begin_load_locked(entry)

    async def _load_session(self, entry: _SessionEntry) -> SessionRuntime:
        return await self.sessions._load_session(entry)

    def _begin_unload_locked(
        self, entry: _SessionEntry, reason: str
    ) -> asyncio.Task[None]:
        return self.sessions._begin_unload_locked(entry, reason)

    async def _unload(self, entry: _SessionEntry, reason: str) -> None:
        return await self.sessions._unload(entry, reason)

    async def _handle_update(self, update: RuntimeUpdate) -> None:
        return await self.updates._handle_update(update)

    async def _update_loop(self) -> None:
        return await self.updates._update_loop()

    def _apply_task_update(
        self, entry: _SessionEntry, update: RuntimeUpdate
    ) -> None:
        return self.updates._apply_task_update(entry, update)

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
        return self.sessions._status(entry)

    def _store(self) -> SessionStore:
        if self._session_store is None:
            raise RuntimeError("AgentRuntime is not started")
        return self._session_store

    async def _import_resources(
        self, runtime: SessionRuntime, resources: tuple[ResourceRef, ...]
    ):
        return await self.resources._import_resources(runtime, resources)

    def _remember_submission(
        self,
        entry: _SessionEntry,
        submission_id: str,
        fingerprint: str,
        accepted: InputAccepted,
    ) -> None:
        return self.submissions._remember_submission(entry, submission_id, fingerprint, accepted)

    def _remember_steer_submission(
        self,
        entry: _SessionEntry,
        submission_id: str,
        fingerprint: str,
        result: TaskOperationResult,
    ) -> None:
        return self.submissions._remember_steer_submission(entry, submission_id, fingerprint, result)

    def _remember_task(self, entry: _SessionEntry, status: TaskStatus) -> None:
        return self.submissions._remember_task(entry, status)

    @staticmethod
    def _has_nonterminal_tasks(entry: _SessionEntry) -> bool:
        return any(
            status.lifecycle in {"accepted", "running"}
            for status in entry.tasks.values()
        )

    @classmethod
    def _has_active_agent_work(cls, entry: _SessionEntry) -> bool:
        if entry.runtime is None:
            return False
        snapshot = entry.runtime.snapshot()
        return bool(
            snapshot.active_task_ids
            or snapshot.queued_task_count
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


AgentRuntime = SessionApi
