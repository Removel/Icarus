"""Update pipeline application behavior."""

from __future__ import annotations
from typing import Any, cast


from agent_app.application.models.status import SessionLifecycle, TaskStatus
from icarus.core.runtime_update import RuntimeUpdate


TRANSIENT_UPDATE_TYPES = frozenset(
    {
        "session.lifecycle",
        "assistant.text_delta",
        "assistant.thinking_delta",
    }
)


from agent_app.application.models.session_entry import _SessionEntry
from agent_app.application.services.common import _safe_error


class UpdatePipeline:
    def __init__(self, *, begin_unload_locked, clock, entries, has_nonterminal_tasks, lifecycle_update, remember_task, store, update_queue, updates, logger):
        self._begin_unload_locked = begin_unload_locked
        self._clock = clock
        self._entries = entries
        self._has_nonterminal_tasks = has_nonterminal_tasks
        self._lifecycle_update = lifecycle_update
        self._remember_task = remember_task
        self._store = store
        self._update_queue = update_queue
        self._updates = updates
        self.logger = logger

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
                                lifecycle_update = cast(SessionLifecycle, projected)
                        if update.type not in TRANSIENT_UPDATE_TYPES:
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
                elif update.type not in TRANSIENT_UPDATE_TYPES:
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
            queue_position = int(cast(Any, update.payload["queue_position"]))
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

