"""Plugin adapter for per-turn Skill retrieval and context publication."""

import asyncio
import json
import logging

from apps.agent.src.agent_orchestration.events import Event
from apps.agent.src.agent_orchestration.plugin_runtime import BasePlugin
from apps.agent.src.agent_orchestration.plugins.blackboard.events import (
    ContextBlock,
    ContextContributionEvent,
)
from apps.agent.src.agent_orchestration.plugins.skill.ranker import SkillRanker
from apps.agent.src.agent_orchestration.plugins.skill.scanner import SkillScanner
from apps.agent.src.agent_orchestration.plugins.skill.session_state import (
    SessionSkillState,
)
from apps.agent.src.agent_orchestration.plugins.skill.usage_store import (
    SkillUsageStore,
)
from apps.agent.src.agent_orchestration.plugins.user_input.events import (
    UserInputEvent,
)
from apps.agent.src.model_provider.base_embedding import BaseEmbedding


_UNCHANGED_CONTEXT = (
    "The available skill context is unchanged from the previous full injection."
)


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
        retrieval_timeout_seconds: float = 30.0,
        logger: logging.Logger | None = None,
    ) -> None:
        super().__init__(plugin_id)
        if not workspace_key.strip():
            raise ValueError("workspace_key cannot be empty")
        if not user_input_plugin_id.strip():
            raise ValueError("user_input_plugin_id cannot be empty")
        if retrieval_timeout_seconds <= 0:
            raise ValueError("retrieval_timeout_seconds must be positive")
        self.workspace_key = workspace_key
        self.user_input_plugin_id = user_input_plugin_id
        self.scanner = scanner
        self.usage_store = usage_store
        self.embedding = embedding
        self.ranker = ranker
        self.session_state = session_state
        self.retrieval_timeout_seconds = retrieval_timeout_seconds
        self.logger = logger or logging.getLogger(__name__)
        self._disabled_reason: str | None = None

    async def consume(self, source_plugin_id: str, event: Event) -> None:
        if (
            source_plugin_id != self.user_input_plugin_id
            or not isinstance(event, UserInputEvent)
        ):
            return
        if self._disabled_reason is not None:
            await self._publish_failure(event, self._disabled_reason)
            return
        try:
            await asyncio.wait_for(
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
        except Exception as error:
            self.logger.exception(
                "Skill retrieval failed: correlation_id=%s",
                event.correlation_id,
            )
            await self._publish_failure(
                event,
                str(error) or type(error).__name__,
            )

    async def stop(self) -> None:
        try:
            await self.embedding.aclose()
        finally:
            usage_store = self.usage_store
            self.usage_store = None
            if usage_store is not None:
                await asyncio.to_thread(usage_store.close)

    async def _retrieve_and_publish(self, event: UserInputEvent) -> None:
        skills = await asyncio.to_thread(self.scanner.scan)
        if not skills:
            update = self.session_state.update([])
            if update.skills:
                await self._publish_update(event, update)
                return
            await self.publish(
                ContextContributionEvent(
                    correlation_id=event.correlation_id,
                    status="completed",
                )
            )
            return

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
                    "Skill usage discovery failed; continuing without usage state"
                )
        query_vector = await self.embedding.embed_query(event.prompt)
        document_vectors = await self.embedding.embed_documents(
            [skill.description for skill in skills]
        )
        ranked = await asyncio.to_thread(
            self.ranker.rank,
            skills,
            query_vector,
            document_vectors,
            usages,
        )
        selected = [item.skill for item in ranked]
        if self.usage_store is not None:
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
        await self._publish_update(event, update)

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
