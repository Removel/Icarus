"""Thread-safe scheduling and lifecycle management for Skill write Jobs."""

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from uuid import uuid4

from apps.agent.src.agent_orchestration.events import Event
from apps.agent.src.agent_orchestration.hooks import hook_context
from apps.agent.src.agent_orchestration.plugins.skill.coordinator import (
    SkillWriteCoordinator,
)
from apps.agent.src.agent_orchestration.plugins.skill.evolver import SkillEvolver
from apps.agent.src.agent_orchestration.plugins.skill.jobs import SkillJob
from apps.agent.src.agent_orchestration.plugins.skill.models import SkillScope
from apps.agent.src.agent_orchestration.plugins.skill.producer import SkillProducer
from apps.agent.src.agent_orchestration.plugins.skill.repository import (
    SkillRepository,
    SkillSnapshot,
)
from apps.agent.src.agent_orchestration.run_control.events import (
    TaskContextInputEvent,
    TaskContextInputResultEvent,
)
from apps.agent.src.model_provider.types import Message


EventPublisher = Callable[[Event], Awaitable[None]]
BackgroundWorkStarter = Callable[
    [str, Callable[[], Awaitable[None]]], asyncio.Task[None]
]
_TERMINAL_LIMIT = 100


class SkillJobManager:
    def __init__(
        self,
        *,
        producer: SkillProducer,
        evolver: SkillEvolver,
        repository: SkillRepository,
        coordinator: SkillWriteCoordinator,
        workspace_dir: str | Path,
        publish_event: EventPublisher | None = None,
        close_resource: Callable[[], Awaitable[None]] | None = None,
        terminal_limit: int = _TERMINAL_LIMIT,
    ) -> None:
        if terminal_limit <= 0:
            raise ValueError("terminal_limit must be positive")
        self._producer = producer
        self._evolver = evolver
        self._repository = repository
        self._coordinator = coordinator
        self._workspace_dir = Path(workspace_dir).expanduser().resolve()
        self._publish_event = publish_event
        self._background_work_starter: BackgroundWorkStarter | None = None
        self._close_resource = close_resource
        self._terminal_limit = terminal_limit
        self._jobs: dict[str, SkillJob] = {}
        self._legacy_workspace_jobs: dict[str, SkillJob] = {}
        self._session_job_ids: list[str] = []
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._stages: dict[str, str] = {}
        self._lock = RLock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._accepting = False
        self._stopped = False

    def bind_publisher(self, publisher: EventPublisher) -> None:
        self._publish_event = publisher

    def bind_background_work_starter(
        self,
        starter: BackgroundWorkStarter,
    ) -> None:
        self._background_work_starter = starter

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._accepting = True

    def submit_produce(
        self,
        *,
        name: str,
        scope: SkillScope,
        instructions: str,
        conversation: Sequence[Message],
        task_id: str,
        run_id: str,
        step: int,
    ) -> SkillJob:
        return self._submit(
            operation="produce",
            name=name,
            scope=scope,
            instructions=instructions,
            conversation=conversation,
            task_id=task_id,
            run_id=run_id,
            step=step,
            snapshot=None,
        )

    def submit_evolve(
        self,
        *,
        name: str,
        instructions: str,
        conversation: Sequence[Message],
        task_id: str,
        run_id: str,
        step: int,
        snapshot: SkillSnapshot,
    ) -> SkillJob:
        return self._submit(
            operation="evolve",
            name=name,
            scope=None,
            instructions=instructions,
            conversation=conversation,
            task_id=task_id,
            run_id=run_id,
            step=step,
            snapshot=snapshot,
        )

    def _submit(
        self,
        *,
        operation: str,
        name: str,
        scope: SkillScope | None,
        instructions: str,
        conversation: Sequence[Message],
        task_id: str,
        run_id: str,
        step: int,
        snapshot: SkillSnapshot | None,
    ) -> SkillJob:
        loop = self._loop
        if loop is None or not loop.is_running():
            raise RuntimeError("Skill Job manager is not running")
        with self._lock:
            if not self._accepting:
                raise RuntimeError("Skill Job manager is quiescing")
            job = SkillJob.create(
                job_id=uuid4().hex,
                operation=operation,
                target_name=name,
                scope=scope,
                task_id=task_id,
                run_id=run_id,
                step=step,
            )
            self._jobs[job.job_id] = job
            self._session_job_ids.append(job.job_id)
            evidence = tuple(conversation)
            loop.call_soon_threadsafe(
                self._schedule,
                job.job_id,
                instructions,
                evidence,
                snapshot,
            )
        return job

    def _schedule(
        self,
        job_id: str,
        instructions: str,
        conversation: tuple[Message, ...],
        snapshot: SkillSnapshot | None,
    ) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.status != "queued":
                return
        starter = self._background_work_starter
        if starter is None:
            raise RuntimeError(
                "Skill Job manager has no background work starter"
            )
        name = f"skill-job:{job_id}"
        try:
            task = starter(
                name,
                lambda: self._run(
                    job_id, instructions, conversation, snapshot
                ),
            )
        except RuntimeError:
            with self._lock:
                current = self._jobs.get(job_id)
                if current is not None and not current.is_terminal:
                    self._jobs[job_id] = current.transition("interrupted")
            return
        self._tasks[job_id] = task
        task.add_done_callback(lambda _: self._tasks.pop(job_id, None))

    async def _run(
        self,
        job_id: str,
        instructions: str,
        conversation: tuple[Message, ...],
        snapshot: SkillSnapshot | None,
    ) -> None:
        self._transition(job_id, "running")
        self._stages[job_id] = "generating"
        draft: Path | None = None
        try:
            job = self.require(job_id)
            if job.operation == "produce":
                assert job.scope is not None
                draft = await self._prepare_draft(
                    self._repository.prepare_produce,
                    job.target_name,
                    job.scope,
                )
            else:
                if snapshot is None:
                    raise ValueError("Evolve Job requires a Skill snapshot")
                draft = await self._prepare_draft(
                    self._repository.prepare_evolve, snapshot
                )
            with hook_context(
                {
                    "task_id": job.task_id,
                    "parent_run_id": job.run_id,
                    "skill_job_id": job.job_id,
                    "agent_kind": "skill_generation",
                    "operation": job.operation,
                    "skill_name": job.target_name,
                },
                run_id=None,
            ):
                if job.operation == "produce":
                    assert job.scope is not None
                    await self._producer.produce(
                        name=job.target_name,
                        scope=job.scope,
                        instructions=instructions,
                        conversation=conversation,
                        **self._generation_paths(draft),
                    )
                else:
                    assert snapshot is not None
                    await self._evolver.evolve(
                        name=job.target_name,
                        instructions=instructions,
                        conversation=conversation,
                        snapshot=snapshot,
                        **self._generation_paths(draft),
                    )
            self._stages[job_id] = "committing"
            path = await self._finish_commit(
                job, snapshot, draft
            )
        except asyncio.CancelledError:
            self._transition_if_active(job_id, "interrupted")
        except Exception as error:
            self._transition_if_active(
                job_id,
                "failed",
                error=f"Skill Job failed ({type(error).__name__})",
            )
        else:
            self._transition(job_id, "succeeded", path=path)
        finally:
            self._stages.pop(job_id, None)
            if draft is not None:
                try:
                    await asyncio.shield(
                        asyncio.to_thread(self._repository.cleanup_draft, draft)
                    )
                except Exception:
                    self._repository.logger.exception(
                        "Unable to clean Skill Draft: job_id=%s", job_id
                    )
        await self._notify(job_id)

    async def _prepare_draft(
        self,
        operation: Callable[..., Path],
        *arguments: object,
    ) -> Path:
        worker = asyncio.create_task(asyncio.to_thread(operation, *arguments))
        try:
            return await asyncio.shield(worker)
        except asyncio.CancelledError:
            try:
                orphan = await worker
            except Exception:
                pass
            else:
                await asyncio.shield(
                    asyncio.to_thread(self._repository.cleanup_draft, orphan)
                )
            raise

    async def _finish_commit(
        self,
        job: SkillJob,
        snapshot: SkillSnapshot | None,
        draft: Path,
    ) -> Path:
        worker = asyncio.create_task(
            asyncio.to_thread(
                self._coordinator.run,
                job.target_name,
                lambda: self._commit(job, snapshot, draft),
            )
        )
        try:
            return await asyncio.shield(worker)
        except asyncio.CancelledError:
            # Once publication starts, report its real outcome instead of
            # abandoning a worker that may already have replaced the target.
            return await worker

    def _commit(
        self,
        job: SkillJob,
        snapshot: SkillSnapshot | None,
        draft: Path,
    ) -> Path:
        if job.operation == "produce":
            assert job.scope is not None
            return self._repository.publish_produce(
                job.target_name, job.scope, draft
            )
        if snapshot is None:
            raise ValueError("Evolve Job requires a Skill snapshot")
        return self._repository.publish_evolve(snapshot, draft)

    def _generation_paths(self, draft: Path) -> dict[str, Path]:
        return {
            "draft_dir": draft,
            "workspace_dir": self._workspace_dir,
            "global_skills_dir": self._repository.global_skills_dir,
            "workspace_skills_dir": self._repository.workspace_skills_dir,
        }

    async def _notify(self, job_id: str) -> None:
        publisher = self._publish_event
        if publisher is None:
            return
        job = self.require(job_id)
        event = TaskContextInputEvent(
            task_id=job.task_id,
            content=self._notification_content(job),
        )
        with self._lock:
            current = self._jobs[job_id]
            self._jobs[job_id] = current.with_notification_request(event.event_id)
        try:
            await publisher(event)
        except Exception:
            # Notification transport is best-effort and does not alter the Job result.
            return

    @staticmethod
    def _notification_content(job: SkillJob) -> str:
        summary = (
            f"Skill {job.operation} job {job.job_id} {job.status}: "
            f"{job.target_name}"
        )
        if job.path is not None:
            return f"{summary} at {job.path}"
        if job.error is not None:
            return f"{summary}. {job.error}"
        return summary

    def record_notification_result(
        self, event: TaskContextInputResultEvent
    ) -> SkillJob | None:
        with self._lock:
            job = next(
                (
                    candidate
                    for candidate in self._jobs.values()
                    if candidate.notification_event_id == event.request_event_id
                ),
                None,
            )
            if job is None:
                return None
            updated = job.with_notification_status(event.status)
            self._jobs[job.job_id] = updated
            return updated

    def get(self, job_id: str) -> SkillJob | None:
        with self._lock:
            return self._jobs.get(job_id)

    def require(self, job_id: str) -> SkillJob:
        job = self.get(job_id)
        if job is None:
            raise KeyError(f"Skill Job is not found: {job_id}")
        return job

    def _transition(self, job_id: str, status: str, **values: object) -> SkillJob:
        with self._lock:
            current = self._jobs[job_id]
            updated = current.transition(status, **values)
            self._jobs[job_id] = updated
            self._prune_locked()
            return updated

    def _transition_if_active(
        self, job_id: str, status: str, **values: object
    ) -> SkillJob:
        with self._lock:
            current = self._jobs[job_id]
            if current.is_terminal:
                return current
        return self._transition(job_id, status, **values)

    def _prune_locked(self) -> None:
        terminal = sorted(
            (job for job in self._jobs.values() if job.is_terminal),
            key=lambda job: (job.finished_at or job.created_at, job.job_id),
        )
        for job in terminal[:-self._terminal_limit]:
            self._jobs.pop(job.job_id, None)
            self._session_job_ids = [
                item for item in self._session_job_ids if item != job.job_id
            ]

    async def quiesce(self) -> None:
        with self._lock:
            self._accepting = False

    async def drain(self) -> None:
        # Generating tasks are cancellable. A commit already running in a worker
        # thread must finish before state can be snapshotted safely.
        await asyncio.sleep(0)
        for job_id, task in tuple(self._tasks.items()):
            if self._stages.get(job_id) != "committing" and not task.done():
                task.cancel()
        tasks = tuple(self._tasks.values())
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        with self._lock:
            for job_id, job in tuple(self._jobs.items()):
                if not job.is_terminal:
                    self._jobs[job_id] = job.transition("interrupted")
            self._prune_locked()

    async def stop(self) -> None:
        if self._stopped:
            return
        self._stopped = True
        await self.quiesce()
        await self.drain()
        if self._close_resource is not None:
            await self._close_resource()

    async def snapshot_workspace_state(self) -> Mapping[str, object] | None:
        return None

    async def snapshot_session_state(self) -> Mapping[str, object]:
        with self._lock:
            ids = [job_id for job_id in self._session_job_ids if job_id in self._jobs]
            return {
                "job_ids": ids,
                "jobs": [self._jobs[job_id].to_dict() for job_id in ids],
                "notifications": {
                    job_id: self._jobs[job_id].notification_status
                    for job_id in ids
                    if self._jobs[job_id].notification_status is not None
                },
            }

    async def restore_workspace_state(
        self, state: Mapping[str, object], *, state_version: int
    ) -> None:
        if state_version != 1:
            raise ValueError("Unsupported Skill workspace state version")
        raw_jobs = state.get("jobs")
        if not isinstance(raw_jobs, list):
            raise ValueError("Skill workspace state requires jobs")
        restored: dict[str, SkillJob] = {}
        now = datetime.now(UTC)
        for raw in raw_jobs:
            if not isinstance(raw, Mapping):
                raise ValueError("Invalid persisted Skill Job entry")
            job = SkillJob.from_dict(raw)
            if not job.is_terminal:
                if job.status == "queued":
                    job = job.transition("interrupted", now=now)
                else:
                    job = job.transition("interrupted", now=now)
            restored[job.job_id] = job
        with self._lock:
            self._legacy_workspace_jobs = restored

    async def restore_session_state(
        self, state: Mapping[str, object], *, state_version: int
    ) -> None:
        if state_version != 1:
            raise ValueError("Unsupported Skill session state version")
        job_ids = state.get("job_ids")
        notifications = state.get("notifications", {})
        if not isinstance(job_ids, list) or not all(
            isinstance(item, str) for item in job_ids
        ):
            raise ValueError("Skill session state requires string job_ids")
        if not isinstance(notifications, Mapping):
            raise ValueError("Skill session notifications must be a mapping")
        raw_jobs = state.get("jobs")
        restored: dict[str, SkillJob] | None = None
        if raw_jobs is not None:
            if not isinstance(raw_jobs, list):
                raise ValueError("Skill session jobs must be a list")
            restored = {}
            now = datetime.now(UTC)
            for raw in raw_jobs:
                if not isinstance(raw, Mapping):
                    raise ValueError("Invalid persisted Skill Job entry")
                job = SkillJob.from_dict(raw)
                if not job.is_terminal:
                    job = job.transition("interrupted", now=now)
                restored[job.job_id] = job
        with self._lock:
            if restored is not None:
                self._jobs = restored
            else:
                self._jobs = {
                    job_id: self._legacy_workspace_jobs[job_id]
                    for job_id in job_ids
                    if job_id in self._legacy_workspace_jobs
                }
            self._session_job_ids = [
                job_id for job_id in job_ids if job_id in self._jobs
            ]
            for job_id, status in notifications.items():
                job = self._jobs.get(str(job_id))
                if job is None or not isinstance(status, str):
                    continue
                if job.notification_event_id is not None:
                    self._jobs[job.job_id] = job.with_notification_status(status)
            self._prune_locked()
            self._legacy_workspace_jobs = {}
