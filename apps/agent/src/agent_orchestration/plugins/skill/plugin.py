"""Plugin adapter for per-turn Skill retrieval and context publication."""

import asyncio
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
import json
import logging
import time

from apps.agent.src.agent_orchestration.capability import (
    AgentCompletedEvent,
)
from apps.agent.src.agent_orchestration.events import Event
from apps.agent.src.agent_orchestration.hooks import HookDispatcher
from apps.agent.src.agent_orchestration.plugin_runtime import BasePlugin
from apps.agent.src.agent_orchestration.plugins.blackboard.events import (
    ContextBlock,
    ContextContributionEvent,
)
from apps.agent.src.agent_orchestration.plugins.skill.coordinator import (
    WorkspaceMaintenanceCoordinator,
)
from apps.agent.src.agent_orchestration.plugins.skill.maintainer import (
    SkillMaintainer,
)
from apps.agent.src.agent_orchestration.plugins.skill.ranker import SkillRanker
from apps.agent.src.agent_orchestration.plugins.skill.ranker import (
    lifecycle_for_usage,
)
from apps.agent.src.agent_orchestration.plugins.skill.repository import (
    RepositoryBatchResult,
    SkillRepository,
)
from apps.agent.src.agent_orchestration.plugins.skill.scanner import SkillScanner
from apps.agent.src.agent_orchestration.plugins.skill.session_state import (
    SessionSkillState,
)
from apps.agent.src.agent_orchestration.plugins.skill.turn_state import (
    SkillTurnState,
    ToolTrajectoryError,
    TurnRecord,
    tool_call_count_from_messages,
    tool_traces_from_messages,
)
from apps.agent.src.agent_orchestration.plugins.skill.usage_store import (
    SkillUsageStore,
)
from apps.agent.src.agent_orchestration.plugins.user_input.events import (
    InputFinishedEvent,
    UserInputEvent,
)
from apps.agent.src.model_provider.base_embedding import BaseEmbedding


_UNCHANGED_CONTEXT = (
    "The available skill context is unchanged from the previous full injection."
)


@dataclass(frozen=True)
class _RetrievalOutcome:
    candidate_count: int
    qualified_count: int
    ranked: tuple
    mode: str
    cumulative_skill_count: int


class SkillPlugin(BasePlugin):
    """Retrieve relevant Skills once for each accepted user input."""

    def __init__(
        self,
        plugin_id: str,
        *,
        workspace_key: str,
        user_input_plugin_id: str,
        scanner: SkillScanner,
        usage_store: SkillUsageStore | None,
        embedding: BaseEmbedding,
        ranker: SkillRanker,
        session_state: SessionSkillState,
        agent_plugin_id: str = "agent",
        maintainer: SkillMaintainer | None = None,
        repository: SkillRepository | None = None,
        coordinator: WorkspaceMaintenanceCoordinator | None = None,
        turn_state: SkillTurnState | None = None,
        hook_dispatcher: HookDispatcher | None = None,
        maintenance_tool_threshold: int = 10,
        retrieval_timeout_seconds: float = 30.0,
        logger: logging.Logger | None = None,
    ) -> None:
        super().__init__(plugin_id)
        if not workspace_key.strip():
            raise ValueError("workspace_key cannot be empty")
        if not user_input_plugin_id.strip():
            raise ValueError("user_input_plugin_id cannot be empty")
        if not agent_plugin_id.strip():
            raise ValueError("agent_plugin_id cannot be empty")
        if maintenance_tool_threshold < 0:
            raise ValueError("maintenance_tool_threshold cannot be negative")
        if retrieval_timeout_seconds <= 0:
            raise ValueError("retrieval_timeout_seconds must be positive")
        maintenance_dependencies = (maintainer, repository, coordinator)
        if any(item is not None for item in maintenance_dependencies) and not all(
            item is not None for item in maintenance_dependencies
        ):
            raise ValueError(
                "maintainer, repository, and coordinator must be provided together"
            )
        self.workspace_key = workspace_key
        self.user_input_plugin_id = user_input_plugin_id
        self.agent_plugin_id = agent_plugin_id
        self.scanner = scanner
        self.usage_store = usage_store
        self.embedding = embedding
        self.ranker = ranker
        self.session_state = session_state
        self.maintainer = maintainer
        self.repository = repository
        self.coordinator = coordinator
        self.turn_state = turn_state or SkillTurnState()
        self.hook_dispatcher = hook_dispatcher
        self.maintenance_tool_threshold = maintenance_tool_threshold
        self.retrieval_timeout_seconds = retrieval_timeout_seconds
        self.logger = logger or logging.getLogger(__name__)
        self._disabled_reason: str | None = None
        self._maintenance_tasks: set[asyncio.Task[None]] = set()
        self._repository_tasks: set[asyncio.Task[RepositoryBatchResult]] = set()
        self._maintenance_repository_tasks: dict[
            asyncio.Task[None],
            asyncio.Task[RepositoryBatchResult],
        ] = {}
        self._repository_maintenance_tasks: dict[
            asyncio.Task[RepositoryBatchResult],
            asyncio.Task[None],
        ] = {}
        self._maintenance_claim_token: str | None = None

    def accepts_event(self, source_plugin_id: str, event: Event) -> bool:
        if source_plugin_id == self.user_input_plugin_id:
            return isinstance(event, UserInputEvent) or (
                isinstance(event, InputFinishedEvent)
                and event.status == "failed"
            )
        if source_plugin_id == self.agent_plugin_id:
            return isinstance(event, AgentCompletedEvent)
        return False

    async def consume(self, source_plugin_id: str, event: Event) -> None:
        if source_plugin_id == self.user_input_plugin_id and isinstance(
            event, UserInputEvent
        ):
            self.turn_state.start(event)
            await self._consume_user_input(event)
            return
        if source_plugin_id == self.user_input_plugin_id and isinstance(
            event, InputFinishedEvent
        ):
            if event.status == "failed":
                self.turn_state.discard(event.correlation_id)
            return
        if source_plugin_id == self.agent_plugin_id and isinstance(
            event, AgentCompletedEvent
        ):
            await self._consume_agent_event(event)

    async def _consume_user_input(self, event: UserInputEvent) -> None:
        started_at = time.monotonic()
        if self._disabled_reason is not None:
            await self._publish_failure(event, self._disabled_reason)
            await self._trigger_retrieval_hook(
                "error",
                self._retrieval_error_data(
                    event,
                    "SkillRetrievalDisabled",
                    self._disabled_reason,
                    started_at,
                ),
            )
            return
        try:
            outcome = await asyncio.wait_for(
                self._retrieve_and_publish(event),
                timeout=self.retrieval_timeout_seconds,
            )
        except TimeoutError:
            self._disabled_reason = (
                "Skill retrieval timed out after "
                f"{self.retrieval_timeout_seconds:g} seconds; "
                "disabled for this runtime"
            )
            self.logger.error(
                "%s: correlation_id=%s",
                self._disabled_reason,
                event.correlation_id,
            )
            await self._publish_failure(event, self._disabled_reason)
            await self._trigger_retrieval_hook(
                "error",
                self._retrieval_error_data(
                    event,
                    "TimeoutError",
                    self._disabled_reason,
                    started_at,
                ),
            )
        except Exception as error:
            self.logger.exception(
                "Skill retrieval failed: correlation_id=%s",
                event.correlation_id,
            )
            await self._publish_failure(
                event,
                str(error) or type(error).__name__,
            )
            await self._trigger_retrieval_hook(
                "error",
                self._retrieval_error_data(
                    event,
                    type(error).__name__,
                    "Skill retrieval failed",
                    started_at,
                ),
            )
        else:
            await self._trigger_retrieval_hook(
                "after",
                {
                    "correlation_id": event.correlation_id,
                    "candidate_count": outcome.candidate_count,
                    "qualified_count": outcome.qualified_count,
                    "minimum_content_score": (
                        self.ranker.minimum_content_score
                    ),
                    "selected": [
                        {
                            "name": item.skill.name,
                            "scope": item.skill.scope,
                            "content_score": item.content_score,
                            "lifecycle_status": item.lifecycle_status,
                            "final_score": item.final_score,
                        }
                        for item in outcome.ranked
                    ],
                    "mode": outcome.mode,
                    "cumulative_skill_count": (
                        outcome.cumulative_skill_count
                    ),
                    "duration_ms": self._elapsed_ms(started_at),
                },
            )

    async def drain(self) -> None:
        if self._maintenance_tasks:
            await asyncio.gather(
                *tuple(self._maintenance_tasks),
                return_exceptions=True,
            )

    async def stop(self) -> None:
        for task in tuple(self._maintenance_tasks):
            task.cancel()
        if self._maintenance_tasks:
            await asyncio.gather(
                *tuple(self._maintenance_tasks),
                return_exceptions=True,
            )
        self._maintenance_tasks.clear()
        if self._repository_tasks:
            await asyncio.gather(
                *tuple(self._repository_tasks),
                return_exceptions=True,
            )
        try:
            await self.embedding.aclose()
        finally:
            usage_store = self.usage_store
            self.usage_store = None
            if usage_store is not None:
                await asyncio.to_thread(usage_store.close)

    async def _retrieve_and_publish(
        self,
        event: UserInputEvent,
    ) -> _RetrievalOutcome:
        skills = await asyncio.to_thread(self.scanner.scan)
        usages = {}
        if skills and self.usage_store is not None:
            try:
                usages = await asyncio.to_thread(
                    self.usage_store.ensure_discovered,
                    self.workspace_key,
                    skills,
                )
            except Exception:
                self.logger.exception(
                    "Skill usage discovery failed; continuing without usage state"
                )
        ranked = []
        qualified_count = 0
        if skills:
            query_vector = await self.embedding.embed_query(event.prompt)
            document_vectors = await self.embedding.embed_documents(
                [skill.description for skill in skills]
            )
            ranked, qualified_count = await asyncio.to_thread(
                self.ranker.rank_with_summary,
                skills,
                query_vector,
                document_vectors,
                usages,
            )
        selected = [item.skill for item in ranked]
        self.turn_state.set_matched_skills(event.correlation_id, selected)
        if selected and self.usage_store is not None:
            try:
                await asyncio.to_thread(
                    self.usage_store.mark_used,
                    self.workspace_key,
                    selected,
                )
            except Exception:
                self.logger.exception(
                    "Skill usage update failed; continuing without persisted usage"
                )
        available_by_name = {
            skill.normalized_name: skill for skill in skills
        }
        reconciled = [
            available_by_name.get(existing.normalized_name, existing)
            for existing in self.session_state.selected_skills
        ]
        update = self.session_state.update([*reconciled, *selected])
        if update.skills:
            await self._publish_update(event, update)
            mode = update.mode
        else:
            await self.publish(
                ContextContributionEvent(
                    correlation_id=event.correlation_id,
                    status="completed",
                )
            )
            mode = "empty"
        return _RetrievalOutcome(
            candidate_count=len(skills),
            qualified_count=qualified_count,
            ranked=tuple(ranked),
            mode=mode,
            cumulative_skill_count=len(self.session_state.selected_skills),
        )

    async def _consume_agent_event(self, event: AgentCompletedEvent) -> None:
        if event.response.finish_reason != "stop":
            self.turn_state.discard(event.correlation_id)
            return
        if not self._maintenance_enabled:
            self.turn_state.discard(event.correlation_id)
            return
        try:
            tool_call_count = tool_call_count_from_messages(
                event.response.messages
            )
            if tool_call_count <= self.maintenance_tool_threshold:
                self.turn_state.discard(event.correlation_id)
                return
            tool_traces = await asyncio.to_thread(
                tool_traces_from_messages,
                event.response.messages,
            )
            turn = self.turn_state.pop_with_tool_traces(
                event.correlation_id,
                tool_traces,
            )
        except ToolTrajectoryError as error:
            self.turn_state.discard(event.correlation_id)
            self.logger.error(
                "Skill maintenance trajectory is invalid: correlation_id=%s: %s",
                event.correlation_id,
                error,
            )
            await self._trigger_maintenance_hook(
                "error",
                {
                    "correlation_id": event.correlation_id,
                    "stage": "trajectory",
                    "error_type": type(error).__name__,
                    "error_message": str(error),
                },
            )
            return
        if turn is None:
            return
        coordinator = self.coordinator
        claim_token = (
            coordinator.claim(self.workspace_key)
            if coordinator is not None
            else None
        )
        if coordinator is None or claim_token is None:
            self.logger.info(
                "Skipping Skill maintenance because Workspace is busy: %s",
                self.workspace_key,
            )
            return
        self._maintenance_claim_token = claim_token

        try:
            messages = await asyncio.to_thread(
                deepcopy,
                event.response.messages,
            )
            session_skills = self.session_state.selected_skills
            parent_run_id = self._current_run_id()
            task = asyncio.create_task(
                self._run_maintenance(
                    turn,
                    messages,
                    session_skills,
                    parent_run_id,
                ),
                name=(
                    f"skill-maintenance:{self.workspace_key}:"
                    f"{event.correlation_id}"
                ),
            )
        except BaseException:
            coordinator.release(self.workspace_key, claim_token)
            self._maintenance_claim_token = None
            raise
        self._maintenance_tasks.add(task)
        task.add_done_callback(self._maintenance_task_completed)

    @property
    def _maintenance_enabled(self) -> bool:
        return (
            self.maintainer is not None
            and self.repository is not None
            and self.coordinator is not None
        )

    async def _run_maintenance(
        self,
        turn: TurnRecord,
        messages,
        session_skills,
        parent_run_id: str | None,
    ) -> None:
        from apps.agent.src.agent_orchestration.hooks import hook_context

        with hook_context(
            {
                "plugin_id": self.plugin_id,
                "workspace_key": self.workspace_key,
                "correlation_id": turn.correlation_id,
                "parent_run_id": parent_run_id,
                "maintenance": True,
            },
            new_run=True,
        ):
            try:
                await self._trigger_maintenance_hook(
                    "before",
                    {
                        "correlation_id": turn.correlation_id,
                        "tool_call_count": turn.tool_call_count,
                        "parent_run_id": parent_run_id,
                    },
                )
                snapshots = await self._build_maintenance_snapshots()
                maintainer = self.maintainer
                repository = self.repository
                assert maintainer is not None
                assert repository is not None
                plan = await maintainer.plan(
                    messages=messages,
                    tool_trace=turn.tool_calls,
                    matched_skills=turn.matched_skills,
                    session_skills=session_skills,
                    skill_snapshots=snapshots,
                )
                result = await self._apply_repository_plan(
                    repository,
                    plan.operations,
                    snapshots,
                )
                await self._reconcile_maintenance_result(result)
                await self._trigger_maintenance_hook(
                    "after",
                    {
                        "correlation_id": turn.correlation_id,
                        "operations": [
                            {
                                "action": item.action,
                                "target_name": item.target_name,
                                "status": item.status,
                                "target_written": item.target_written,
                                "file_deleted": item.file_deleted,
                                "deleted_sources": item.deleted_sources,
                                "cleanup_errors": item.cleanup_errors,
                            }
                            for item in result.results
                        ],
                    },
                )
            except asyncio.CancelledError:
                raise
            except Exception as error:
                self.logger.exception(
                    "Skill maintenance failed: correlation_id=%s",
                    turn.correlation_id,
                )
                await self._trigger_maintenance_hook(
                    "error",
                    {
                        "correlation_id": turn.correlation_id,
                        "error_type": type(error).__name__,
                        "error_message": str(error),
                    },
                )

    async def _apply_repository_plan(
        self,
        repository: SkillRepository,
        operations,
        snapshots,
    ) -> RepositoryBatchResult:
        task = asyncio.create_task(
            asyncio.to_thread(
                repository.apply,
                operations,
                snapshots,
            ),
            name=f"skill-repository:{self.workspace_key}",
        )
        maintenance_task = asyncio.current_task()
        if maintenance_task is None:
            task.cancel()
            raise RuntimeError("repository apply requires a maintenance task")
        self._repository_tasks.add(task)
        self._maintenance_repository_tasks[maintenance_task] = task
        self._repository_maintenance_tasks[task] = maintenance_task
        task.add_done_callback(self._repository_task_completed)
        return await asyncio.shield(task)

    async def _build_maintenance_snapshots(self):
        repository = self.repository
        assert repository is not None
        skills = await asyncio.to_thread(self.scanner.scan)
        usages = {}
        if self.usage_store is not None:
            try:
                usages = await asyncio.to_thread(
                    self.usage_store.ensure_discovered,
                    self.workspace_key,
                    skills,
                )
            except Exception:
                self.logger.exception(
                    "Skill usage snapshot failed; using active lifecycle defaults"
                )
        now = datetime.now(UTC)
        lifecycle = {
            skill.skill_key: lifecycle_for_usage(
                usages.get(skill.skill_key),
                now,
            )[0]
            for skill in skills
        }
        return await asyncio.to_thread(
            repository.snapshot,
            lifecycle_by_skill_key=lifecycle,
            usage_by_skill_key=usages,
        )

    async def _reconcile_maintenance_result(
        self,
        result: RepositoryBatchResult,
    ) -> None:
        usage_store = self.usage_store
        if usage_store is None:
            return
        written_names = {
            item.target_name
            for item in result.results
            if item.target_written
        }
        deleted_names = {
            source
            for item in result.results
            for source in item.deleted_sources
        }
        if written_names:
            current = await asyncio.to_thread(self.scanner.scan)
            written = [
                skill
                for skill in current
                if skill.normalized_name in written_names
            ]
            if written:
                try:
                    await asyncio.to_thread(
                        usage_store.activate_after_maintenance,
                        self.workspace_key,
                        written,
                    )
                except Exception:
                    self.logger.exception(
                        "Failed to activate maintained Skill usage state"
                    )
        removed_keys = [
            f"workspace:{name}"
            for name in deleted_names - written_names
        ]
        if removed_keys:
            try:
                await asyncio.to_thread(
                    usage_store.remove,
                    self.workspace_key,
                    removed_keys,
                )
            except Exception:
                self.logger.exception(
                    "Failed to remove deleted Skill usage state"
                )

    async def _trigger_maintenance_hook(
        self,
        phase: str,
        data: dict,
    ) -> None:
        if self.hook_dispatcher is not None:
            await self.hook_dispatcher.atrigger(
                "skill.maintenance",
                phase,
                data,
            )

    async def _trigger_retrieval_hook(
        self,
        phase: str,
        data: dict,
    ) -> None:
        if self.hook_dispatcher is not None:
            await self.hook_dispatcher.atrigger(
                "skill.retrieval",
                phase,
                data,
            )

    @staticmethod
    def _elapsed_ms(started_at: float) -> int:
        return max(0, round((time.monotonic() - started_at) * 1000))

    def _retrieval_error_data(
        self,
        event: UserInputEvent,
        error_type: str,
        error_message: str,
        started_at: float,
    ) -> dict:
        return {
            "correlation_id": event.correlation_id,
            "error_type": error_type,
            "error_message": error_message,
            "duration_ms": self._elapsed_ms(started_at),
        }

    def _maintenance_task_completed(
        self,
        task: asyncio.Task[None],
    ) -> None:
        self._maintenance_tasks.discard(task)
        repository_task = self._maintenance_repository_tasks.pop(task, None)
        if repository_task is None:
            self._release_maintenance_claim()
            return
        if repository_task.done():
            self._release_maintenance_claim()
            self._repository_maintenance_tasks.pop(repository_task, None)
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            self.logger.error(
                "Unexpected Skill maintenance task failure",
                exc_info=(type(error), error, error.__traceback__),
            )

    def _repository_task_completed(
        self,
        task: asyncio.Task[RepositoryBatchResult],
    ) -> None:
        self._repository_tasks.discard(task)
        maintenance_task = self._repository_maintenance_tasks.pop(task, None)
        if maintenance_task is None:
            self._release_maintenance_claim()
            return
        if maintenance_task.done():
            self._release_maintenance_claim()
            self._maintenance_repository_tasks.pop(maintenance_task, None)
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            self.logger.error(
                "Unexpected Skill repository task failure",
                exc_info=(type(error), error, error.__traceback__),
            )

    def _release_maintenance_claim(self) -> None:
        token = self._maintenance_claim_token
        self._maintenance_claim_token = None
        if self.coordinator is not None and token is not None:
            self.coordinator.release(self.workspace_key, token)

    @staticmethod
    def _current_run_id() -> str | None:
        from apps.agent.src.agent_orchestration.hooks import get_hook_context

        context = get_hook_context()
        return context.run_id if context is not None else None

    async def _publish_update(self, event, update) -> None:
        if update.mode == "full":
            content = json.dumps(
                [
                    {
                        "description": skill.description,
                        "name": skill.name,
                        "path": str(skill.path),
                    }
                    for skill in update.skills
                ],
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        else:
            content = _UNCHANGED_CONTEXT

        await self.publish(
            ContextContributionEvent(
                correlation_id=event.correlation_id,
                status="completed",
                context_blocks=[
                    ContextBlock(
                        source_plugin_id=self.plugin_id,
                        context_type="skills",
                        content=content,
                        metadata={"mode": update.mode},
                    )
                ],
            )
        )

    async def _publish_failure(
        self,
        event: UserInputEvent,
        error: str,
    ) -> None:
        await self.publish(
            ContextContributionEvent(
                correlation_id=event.correlation_id,
                status="failed",
                error=error,
            )
        )
