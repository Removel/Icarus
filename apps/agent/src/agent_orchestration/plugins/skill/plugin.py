"""Agent-visible Skill management Plugin."""

from collections.abc import Mapping, Sequence
import time

from apps.agent.src.agent_orchestration.events import Event
from apps.agent.src.agent_orchestration.hooks import HookDispatcher
from apps.agent.src.agent_orchestration.plugin_runtime import BasePlugin
from apps.agent.src.agent_orchestration.plugins.skill.catalog import (
    CatalogScope,
    SkillCatalog,
)
from apps.agent.src.agent_orchestration.plugins.skill.job_manager import (
    SkillJobManager,
)
from apps.agent.src.agent_orchestration.plugins.skill.jobs import SkillJob
from apps.agent.src.agent_orchestration.plugins.skill.models import SkillScope
from apps.agent.src.agent_orchestration.plugins.skill.repository import SkillRepository
from apps.agent.src.agent_orchestration.run_control import (
    TaskContextInputResultEvent,
)
from apps.agent.src.model_provider.types import Message


class SkillOperationError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class SkillPlugin(BasePlugin):
    """Expose explicit Skill discovery and background write operations."""

    def __init__(
        self,
        plugin_id: str,
        *,
        catalog: SkillCatalog,
        repository: SkillRepository,
        job_manager: SkillJobManager,
        conversation: object,
        allow_produce: bool = False,
        allow_evolve: bool = False,
        agent_plugin_id: str = "agent",
        hook_dispatcher: HookDispatcher | None = None,
    ) -> None:
        super().__init__(plugin_id)
        get_messages = getattr(conversation, "get_messages", None)
        if not callable(get_messages):
            raise TypeError(
                "conversation capability must expose get_messages()"
            )
        if not isinstance(allow_produce, bool) or not isinstance(
            allow_evolve, bool
        ):
            raise TypeError("Skill write permissions must be booleans")
        self.catalog = catalog
        self.repository = repository
        self.job_manager = job_manager
        self.conversation = conversation
        self.allow_produce = allow_produce
        self.allow_evolve = allow_evolve
        self.agent_plugin_id = agent_plugin_id
        self.hook_dispatcher = hook_dispatcher
        self.job_manager.bind_publisher(self.publish)

    async def start(self) -> None:
        self.job_manager.bind_background_work_starter(
            lambda name, operation: self.start_background_work(
                operation, name=name
            )
        )
        await self.job_manager.start()

    def accepts_event(self, source_plugin_id: str, event: Event) -> bool:
        return source_plugin_id == self.agent_plugin_id and isinstance(
            event, TaskContextInputResultEvent
        )

    async def consume(self, source_plugin_id: str, event: Event) -> None:
        del source_plugin_id
        if isinstance(event, TaskContextInputResultEvent):
            self.job_manager.record_notification_result(event)

    def list_skills(
        self, scope: CatalogScope = "all"
    ) -> list[dict[str, str]]:
        started_at = time.monotonic()
        try:
            items = [
                self._skill_item(skill)
                for skill in self.catalog.list_skills(scope)
            ]
        except Exception as error:
            self._hook(
                "skill.list",
                "error",
                {
                    "scope": scope,
                    "duration_ms": self._elapsed_ms(started_at),
                    "error_type": type(error).__name__,
                },
            )
            raise
        self._hook(
            "skill.list",
            "after",
            {
                "scope": scope,
                "count": len(items),
                "duration_ms": self._elapsed_ms(started_at),
            },
        )
        return items

    def search(self, keywords: Sequence[str]) -> list[dict[str, str]]:
        started_at = time.monotonic()
        try:
            items = [
                self._skill_item(skill) for skill in self.catalog.search(keywords)
            ]
        except Exception as error:
            self._hook(
                "skill.search",
                "error",
                {
                    "keyword_count": (
                        len(keywords) if not isinstance(keywords, str) else 0
                    ),
                    "duration_ms": self._elapsed_ms(started_at),
                    "error_type": type(error).__name__,
                },
            )
            raise
        self._hook(
            "skill.search",
            "after",
            {
                "keyword_count": len(keywords),
                "result_names": [item["name"] for item in items],
                "duration_ms": self._elapsed_ms(started_at),
            },
        )
        return items

    def produce(
        self,
        *,
        name: str,
        scope: SkillScope,
        instructions: str,
        task_id: str | None,
        run_id: str | None,
        step: int | None,
        task_messages: tuple[Message, ...],
    ) -> dict[str, object]:
        if not self.allow_produce:
            raise SkillOperationError(
                "disabled_by_policy", "Skill production is disabled by policy"
            )
        self._validate_write_context(task_id, run_id, step, task_messages)
        conflicts = self.repository.find_conflicts(name)
        if conflicts:
            raise SkillOperationError(
                "target_conflict",
                f"Skill {name.strip()!r} already exists in "
                f"{', '.join(conflicts)}; use skill_evolve",
            )
        job = self.job_manager.submit_produce(
            name=name.strip(),
            scope=scope,
            instructions=instructions,
            conversation=self._conversation_evidence(task_messages),
            task_id=task_id,
            run_id=run_id,
            step=step,
        )
        self._hook_job_created(job)
        return {"job_id": job.job_id, "status": job.status}

    def evolve(
        self,
        *,
        name: str,
        instructions: str,
        task_id: str | None,
        run_id: str | None,
        step: int | None,
        task_messages: tuple[Message, ...],
    ) -> dict[str, object]:
        if not self.allow_evolve:
            raise SkillOperationError(
                "disabled_by_policy", "Skill evolution is disabled by policy"
            )
        self._validate_write_context(task_id, run_id, step, task_messages)
        visible = self.catalog.find_visible(name)
        if visible is None:
            raise SkillOperationError(
                "target_not_found", f"Skill {name.strip()!r} is not found"
            )
        snapshot = self.repository.capture(visible.normalized_name)
        if snapshot is None:
            raise SkillOperationError(
                "target_not_found",
                f"Skill {name.strip()!r} changed during precheck",
            )
        job = self.job_manager.submit_evolve(
            name=visible.normalized_name,
            instructions=instructions,
            conversation=self._conversation_evidence(task_messages),
            task_id=task_id,
            run_id=run_id,
            step=step,
            snapshot=snapshot,
        )
        self._hook_job_created(job)
        return {"job_id": job.job_id, "status": job.status}

    def job_status(self, job_id: str) -> dict[str, object]:
        job = self.job_manager.get(job_id)
        if job is None:
            raise SkillOperationError(
                "job_not_found", f"Skill Job is not found: {job_id}"
            )
        value = self._job_item(job)
        self._hook(
            "skill.job_status",
            "after",
            {"job_id": job.job_id, "status": job.status},
        )
        return value

    async def quiesce(self) -> None:
        await self.job_manager.quiesce()

    async def drain(self) -> None:
        await self.job_manager.drain()

    async def stop(self) -> None:
        await self.job_manager.stop()

    async def restore_workspace_state(
        self, state: Mapping[str, object], *, state_version: int
    ) -> None:
        await self.job_manager.restore_workspace_state(
            state, state_version=state_version
        )

    async def restore_session_state(
        self, state: Mapping[str, object], *, state_version: int
    ) -> None:
        await self.job_manager.restore_session_state(
            state, state_version=state_version
        )

    async def snapshot_workspace_state(self) -> Mapping[str, object]:
        return await self.job_manager.snapshot_workspace_state()

    async def snapshot_session_state(self) -> Mapping[str, object]:
        return await self.job_manager.snapshot_session_state()

    def _conversation_evidence(
        self, task_messages: tuple[Message, ...]
    ) -> tuple[Message, ...]:
        return tuple(self.conversation.get_messages()) + task_messages

    @staticmethod
    def _validate_write_context(
        task_id: str | None,
        run_id: str | None,
        step: int | None,
        task_messages: tuple[Message, ...],
    ) -> None:
        if not isinstance(task_id, str) or not task_id.strip():
            raise SkillOperationError(
                "invalid_task_context", "Skill writes require task_id"
            )
        if not isinstance(run_id, str) or not run_id.strip():
            raise SkillOperationError(
                "invalid_task_context", "Skill writes require run_id"
            )
        if not isinstance(step, int) or isinstance(step, bool) or step < 0:
            raise SkillOperationError(
                "invalid_task_context", "Skill writes require a valid step"
            )
        if not task_messages or any(
            not isinstance(message, Message) for message in task_messages
        ):
            raise SkillOperationError(
                "invalid_task_context",
                "Skill writes require current task messages",
            )

    @staticmethod
    def _skill_item(skill) -> dict[str, str]:
        return {
            "name": skill.name,
            "description": skill.description,
            "scope": skill.scope,
            "path": str(skill.path),
        }

    @staticmethod
    def _job_item(job: SkillJob) -> dict[str, object]:
        return {
            "job_id": job.job_id,
            "operation": job.operation,
            "status": job.status,
            "target_name": job.target_name,
            "scope": job.scope,
            "path": job.path,
            "error": job.error,
            "created_at": job.created_at.isoformat(),
            "started_at": (
                job.started_at.isoformat() if job.started_at is not None else None
            ),
            "finished_at": (
                job.finished_at.isoformat() if job.finished_at is not None else None
            ),
            "notification_status": job.notification_status,
        }

    def _hook_job_created(self, job: SkillJob) -> None:
        self._hook(
            f"skill.{job.operation}",
            "queued",
            {
                "job_id": job.job_id,
                "target_name": job.target_name,
                "scope": job.scope,
                "status": job.status,
            },
        )

    def _hook(self, name: str, phase: str, data: dict[str, object]) -> None:
        if self.hook_dispatcher is not None:
            self.hook_dispatcher.trigger(name, phase, data)

    @staticmethod
    def _elapsed_ms(started_at: float) -> int:
        return max(0, round((time.monotonic() - started_at) * 1000))
