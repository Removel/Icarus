"""汇聚 Agent 上下文的 BlackboardPlugin。"""

from collections.abc import Mapping, Sequence

from apps.agent.src.agent_orchestration.capability import (
    AgentCancelledEvent,
    AgentCompletedEvent,
)
from apps.agent.src.agent_orchestration.events import Event, TaskErrorEvent
from apps.agent.src.agent_orchestration.plugin_runtime import BasePlugin
from apps.agent.src.model_config import LLMRole
from apps.agent.src.agent_orchestration.plugins.blackboard.state import (
    BlackboardTaskState,
)
from apps.agent.src.agent_orchestration.plugins.blackboard.events import (
    BlackboardCompactedEvent,
    BlackboardContextReadyEvent,
    ContextContributionEvent,
)
from apps.agent.src.agent_orchestration.plugins.blackboard.history_compactor import (
    HistoryCompactor,
)
from apps.agent.src.agent_orchestration.plugins.blackboard.prompt_composer import (
    BlackboardPromptComposer,
)
from apps.agent.src.agent_orchestration.plugins.user_input.events import (
    InputFinishedEvent,
    UserInputEvent,
)
from apps.agent.src.model_provider.types import (
    ImagePart,
    Message,
    TextPart,
    ToolCall,
    Usage,
)


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
        initial_messages: list[Message] | None = None,
        prompt_composer: BlackboardPromptComposer | None = None,
        context_window: int | None = None,
        history_compactor: HistoryCompactor | None = None,
    ) -> None:
        super().__init__(plugin_id)
        self.required_context_sources = frozenset(required_context_sources)
        self.agent_plugin_id = agent_plugin_id
        self.model_role = model_role
        self.system_prompt = system_prompt
        self.tools = None if tools is None else list(tools)
        self.prompt_composer = prompt_composer or BlackboardPromptComposer()
        self.context_window = context_window
        self.history_compactor = history_compactor
        self._messages = list(initial_messages or [])
        self._context_tokens: int | None = None
        self._tasks: dict[str, BlackboardTaskState] = {}

    async def consume(
        self,
        source_plugin_id: str,
        event: Event,
    ) -> None:
        task_id = self._require_task_id(event)
        if isinstance(event, InputFinishedEvent):
            state = self._tasks.get(task_id)
            if state is None:
                return
            state.input_finished = True
            if event.status == "cancelled" and event.run_id is None:
                state.agent_finished = True
            self._remove_task_if_finished(state)
            return

        if source_plugin_id == self.agent_plugin_id:
            state = self._tasks.get(task_id)
            if state is None:
                return
            if isinstance(event, AgentCompletedEvent):
                task_messages = event.response.task_messages
                if not task_messages:
                    task_messages = self._fallback_completed_messages(
                        state,
                        event,
                    )
                committed = self._commit_task_messages(state, task_messages)
                await self._update_context_tokens(
                    state.task_id,
                    event.response.last_usage,
                    committed,
                )
                state.agent_finished = True
            elif isinstance(event, TaskErrorEvent) and event.fatal:
                committed = self._commit_task_messages(
                    state, event.task_messages
                )
                await self._update_context_tokens(
                    state.task_id,
                    event.last_usage,
                    committed,
                )
                state.agent_finished = True
            elif isinstance(event, AgentCancelledEvent):
                committed = self._commit_task_messages(
                    state, event.task_messages
                )
                await self._update_context_tokens(
                    state.task_id,
                    event.last_usage,
                    committed,
                )
                state.agent_finished = True
            self._remove_task_if_finished(state)
            return

        state = self._tasks.setdefault(
            task_id,
            BlackboardTaskState(task_id=task_id),
        )

        if isinstance(event, UserInputEvent):
            if state.user_input is not None:
                raise ValueError(
                    f"User input already exists: task_id={task_id}"
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
            if (
                event.status == "failed"
                and source_plugin_id not in state.reported_context_errors
            ):
                state.reported_context_errors.add(source_plugin_id)
                await self.publish(
                    TaskErrorEvent(
                        task_id=task_id,
                        fatal=False,
                        code="context_provider_failed",
                        error_type="ContextProviderError",
                        error_message=event.error or "context loading failed",
                    )
                )
            await self._publish_if_ready(state)
            return

    def accepts_event(self, source_plugin_id: str, event: Event) -> bool:
        if source_plugin_id == self.plugin_id:
            return False
        if isinstance(event, TaskErrorEvent):
            return source_plugin_id == self.agent_plugin_id
        return True

    def get_task_state(self, task_id: str) -> BlackboardTaskState:
        try:
            return self._tasks[task_id]
        except KeyError as error:
            raise KeyError(
                f"Blackboard task is not found: {task_id}"
            ) from error

    def remove_task(self, task_id: str) -> BlackboardTaskState:
        try:
            return self._tasks.pop(task_id)
        except KeyError as error:
            raise KeyError(
                f"Blackboard task is not found: {task_id}"
            ) from error

    def get_messages(self) -> list[Message]:
        return list(self._messages)

    @property
    def context_tokens(self) -> int | None:
        return self._context_tokens

    async def restore_workspace_state(
        self, state: Mapping[str, object], *, state_version: int
    ) -> None:
        del state, state_version

    async def restore_session_state(
        self, state: Mapping[str, object], *, state_version: int
    ) -> None:
        if state_version != 1:
            raise ValueError("Unsupported Blackboard session state version")
        messages = state.get("messages")
        if not isinstance(messages, list):
            raise ValueError("Blackboard session state requires messages")
        self._messages = [_deserialize_message(item) for item in messages]
        context_tokens = state.get("context_tokens")
        if context_tokens is not None and (
            isinstance(context_tokens, bool)
            or not isinstance(context_tokens, int)
            or context_tokens < 0
        ):
            raise ValueError("Blackboard context_tokens must be non-negative")
        self._context_tokens = context_tokens

    async def snapshot_workspace_state(self) -> Mapping[str, object] | None:
        return None

    async def snapshot_session_state(self) -> Mapping[str, object] | None:
        return {
            "messages": [_serialize_message(item) for item in self._messages],
            "context_tokens": self._context_tokens,
        }

    async def stop(self) -> None:
        if self.history_compactor is not None:
            await self.history_compactor.aclose()

    async def _publish_if_ready(self, state: BlackboardTaskState) -> None:
        if state.context_published or not state.is_context_ready(
            self.required_context_sources
        ):
            return
        user_input = state.user_input
        if user_input is None:
            return
        if (
            self.context_window is not None
            and len(user_input.prompt.encode("utf-8"))
            >= self.context_window * 4
        ):
            await self._fail_before_agent(
                state,
                code="input_too_long",
                error_type="InputTooLongError",
                message="input is too large for the configured context window",
            )
            return
        if self._should_compact():
            try:
                assert self.history_compactor is not None
                summary, usage = await self.history_compactor.compact(
                    self.get_messages()
                )
            except Exception as error:
                await self._fail_before_agent(
                    state,
                    code="compact_failed",
                    error_type=type(error).__name__,
                    message=str(error),
                )
                return
            before_tokens = self._context_tokens
            self._messages = [summary]
            self._context_tokens = usage.output_tokens
            await self.publish(
                BlackboardCompactedEvent(
                    task_id=state.task_id,
                    before_tokens=before_tokens or 0,
                    after_tokens=usage.output_tokens,
                )
            )

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
        input_prompt = self.prompt_composer.compose(
            prompt=user_input.prompt,
            context_blocks=context_blocks,
            context_errors=context_errors,
        )
        state.input_prompt = input_prompt
        context_event = BlackboardContextReadyEvent(
            task_id=state.task_id,
            model_role=self.model_role,
            system_prompt=self.system_prompt,
            input_prompt=input_prompt,
            history_messages=self.get_messages(),
            input_images=user_input.input_images,
            tools=self.tools,
        )
        state.context_published = True
        await self.publish(context_event)

    def _should_compact(self) -> bool:
        return (
            bool(self._messages)
            and self.history_compactor is not None
            and self.context_window is not None
            and self._context_tokens is not None
            and self._context_tokens >= self.context_window * 0.85
        )

    async def _fail_before_agent(
        self,
        state: BlackboardTaskState,
        *,
        code: str,
        error_type: str,
        message: str,
    ) -> None:
        state.context_published = True
        state.agent_finished = True
        await self.publish(
            TaskErrorEvent(
                task_id=state.task_id,
                fatal=True,
                code=code,
                error_type=error_type,
                error_message=message,
            )
        )

    async def _update_context_tokens(
        self,
        task_id: str,
        usage: Usage | None,
        history_committed: bool,
    ) -> None:
        if not history_committed or self.context_window is None:
            return
        if usage is not None:
            self._context_tokens = usage.total_tokens
            return
        await self.publish(
            TaskErrorEvent(
                task_id=task_id,
                fatal=False,
                code="usage_unavailable",
                error_type="UsageUnavailableError",
                error_message="model response did not include usage",
            )
        )

    def _commit_task_messages(
        self,
        state: BlackboardTaskState,
        messages: Sequence[Message],
    ) -> bool:
        if state.history_committed or not messages:
            return False
        self._messages.extend(messages)
        state.history_committed = True
        return True

    @staticmethod
    def _fallback_completed_messages(
        state: BlackboardTaskState,
        event: AgentCompletedEvent,
    ) -> tuple[Message, ...]:
        if state.user_input is None or state.input_prompt is None:
            return ()
        user_content = [
            TextPart(state.input_prompt),
            *state.user_input.input_images,
        ]
        return (
            Message("user", user_content),
            event.response.message,
        )

    def _remove_task_if_finished(self, state: BlackboardTaskState) -> None:
        if state.agent_finished and state.input_finished:
            self._tasks.pop(state.task_id, None)

    @staticmethod
    def _require_task_id(event: Event) -> str:
        if not event.task_id:
            raise ValueError(
                f"Blackboard Event requires task_id: {type(event).__name__}"
            )
        return event.task_id


def _serialize_message(message: Message) -> dict[str, object]:
    content = []
    for part in message.content:
        if isinstance(part, TextPart):
            content.append({"type": "text", "text": part.text})
        elif isinstance(part, ImagePart):
            content.append(
                {
                    "type": "image",
                    "source": part.source,
                    "source_type": part.source_type,
                    "media_type": part.media_type,
                }
            )
        else:
            raise TypeError(f"Unsupported Message content: {type(part).__name__}")
    return {
        "role": message.role,
        "content": content,
        "tool_calls": [
            {
                "id": call.id,
                "name": call.name,
                "arguments": dict(call.arguments),
            }
            for call in message.tool_calls
        ],
        "tool_call_id": message.tool_call_id,
    }


def _deserialize_message(value: object) -> Message:
    if not isinstance(value, Mapping):
        raise ValueError("Blackboard message state must be an object")
    content_value = value.get("content")
    calls_value = value.get("tool_calls", [])
    if not isinstance(content_value, list) or not isinstance(calls_value, list):
        raise ValueError("Blackboard message state has invalid collections")
    content = []
    for part in content_value:
        if not isinstance(part, Mapping):
            raise ValueError("Blackboard content part must be an object")
        if part.get("type") == "text":
            content.append(TextPart(str(part.get("text", ""))))
        elif part.get("type") == "image":
            source = part.get("source", part.get("url", ""))
            source_type = part.get("source_type", "url")
            if source_type not in {"url", "asset"}:
                raise ValueError("Blackboard image source type is invalid")
            content.append(
                ImagePart(
                    source=str(source),
                    source_type=source_type,
                    media_type=(
                        str(part["media_type"])
                        if part.get("media_type") is not None
                        else None
                    ),
                )
            )
        else:
            raise ValueError("Blackboard content part type is invalid")
    tool_calls = []
    for call in calls_value:
        if not isinstance(call, Mapping) or not isinstance(
            call.get("arguments"), Mapping
        ):
            raise ValueError("Blackboard ToolCall state is invalid")
        tool_calls.append(
            ToolCall(
                str(call.get("id", "")),
                str(call.get("name", "")),
                dict(call["arguments"]),
            )
        )
    return Message(
        role=value.get("role"),
        content=content,
        tool_calls=tool_calls,
        tool_call_id=(
            str(value["tool_call_id"])
            if value.get("tool_call_id") is not None
            else None
        ),
    )
