"""汇聚 Agent 上下文的 BlackboardPlugin。"""

from apps.agent.src.agent_orchestration.capability import (
    AgentCompletedEvent,
    AgentErrorEvent,
)
from apps.agent.src.agent_orchestration.events import Event
from apps.agent.src.agent_orchestration.plugin_runtime import BasePlugin
from apps.agent.src.model_config import LLMRole
from apps.agent.src.agent_orchestration.plugins.blackboard.state import (
    BlackboardTaskState,
)
from apps.agent.src.agent_orchestration.plugins.blackboard.events import (
    BlackboardContextReadyEvent,
    ContextContributionEvent,
)
from apps.agent.src.agent_orchestration.plugins.user_input.events import UserInputEvent


class BlackboardPlugin(BasePlugin):
    """等待固定上下文来源，并发布一次完整 Agent 上下文快照。"""

    def __init__(
        self,
        plugin_id: str,
        required_context_sources: set[str] | frozenset[str],
        agent_plugin_id: str = "agent",
        model_role: LLMRole = "thinking",
        system_prompt: str = "",
        tools: list[str] | None = None,
    ) -> None:
        super().__init__(plugin_id)
        self.required_context_sources = frozenset(required_context_sources)
        self.agent_plugin_id = agent_plugin_id
        self.model_role = model_role
        self.system_prompt = system_prompt
        self.tools = None if tools is None else list(tools)
        self._tasks: dict[str, BlackboardTaskState] = {}

    async def consume(
        self,
        source_plugin_id: str,
        event: Event,
    ) -> None:
        correlation_id = self._require_correlation_id(event)
        state = self._tasks.setdefault(
            correlation_id,
            BlackboardTaskState(correlation_id=correlation_id),
        )

        if isinstance(event, UserInputEvent):
            if state.user_input is not None:
                raise ValueError(
                    f"User input already exists: correlation_id={correlation_id}"
                )
            state.user_input = event
            await self._publish_if_ready(state)
            return

        if isinstance(event, ContextContributionEvent):
            if source_plugin_id not in self.required_context_sources:
                return
            mismatched_blocks = [
                block
                for block in event.context_blocks
                if block.source_plugin_id != source_plugin_id
            ]
            if mismatched_blocks:
                raise ValueError(
                    "ContextBlock source does not match publisher: "
                    f"publisher={source_plugin_id}"
                )
            state.contributions[source_plugin_id] = event
            await self._publish_if_ready(state)
            return

        if source_plugin_id != self.agent_plugin_id:
            return
        if isinstance(event, AgentCompletedEvent):
            state.agent_status = "completed"
            state.agent_response = event.response
            state.agent_error = None
        elif isinstance(event, AgentErrorEvent):
            state.agent_status = "failed"
            state.agent_error = event.error_message

    def get_task_state(self, correlation_id: str) -> BlackboardTaskState:
        try:
            return self._tasks[correlation_id]
        except KeyError as error:
            raise KeyError(
                f"Blackboard task is not found: {correlation_id}"
            ) from error

    def remove_task(self, correlation_id: str) -> BlackboardTaskState:
        try:
            return self._tasks.pop(correlation_id)
        except KeyError as error:
            raise KeyError(
                f"Blackboard task is not found: {correlation_id}"
            ) from error

    async def _publish_if_ready(self, state: BlackboardTaskState) -> None:
        if state.context_published or not state.is_context_ready(
            self.required_context_sources
        ):
            return
        user_input = state.user_input
        if user_input is None:
            return

        context_blocks = [
            block
            for contribution in state.contributions.values()
            if contribution.status == "completed"
            for block in contribution.context_blocks
        ]
        context_errors = {
            source_plugin_id: contribution.error or "context loading failed"
            for source_plugin_id, contribution in state.contributions.items()
            if contribution.status == "failed"
        }
        context_event = BlackboardContextReadyEvent(
            correlation_id=state.correlation_id,
            model_role=self.model_role,
            system_prompt=self.system_prompt,
            history_messages=user_input.history_messages,
            prompt=user_input.prompt,
            input_images=user_input.input_images,
            tools=self.tools,
            context_blocks=context_blocks,
            context_errors=context_errors,
        )
        state.context_published = True
        state.agent_status = "running"
        await self.publish(context_event)

    @staticmethod
    def _require_correlation_id(event: Event) -> str:
        if not event.correlation_id:
            raise ValueError(
                f"Blackboard Event requires correlation_id: {type(event).__name__}"
            )
        return event.correlation_id
