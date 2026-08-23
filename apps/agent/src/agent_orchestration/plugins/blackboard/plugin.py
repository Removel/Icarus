"""汇聚 Agent 上下文的 BlackboardPlugin。"""

from collections.abc import Mapping, Sequence

from apps.agent.src.agent_orchestration.capability import (
    AgentCancelledEvent,
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
    ) -> None:
        super().__init__(plugin_id)
        self.required_context_sources = frozenset(required_context_sources)
        self.agent_plugin_id = agent_plugin_id
        self.model_role = model_role
        self.system_prompt = system_prompt
        self.tools = None if tools is None else list(tools)
        self.prompt_composer = prompt_composer or BlackboardPromptComposer()
        self._messages = list(initial_messages or [])
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
                self._commit_task_messages(state, task_messages)
                state.agent_finished = True
            elif isinstance(event, AgentErrorEvent):
                state.agent_finished = True
            elif isinstance(event, AgentCancelledEvent):
                self._commit_task_messages(state, event.task_messages)
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
            await self._publish_if_ready(state)
            return

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

    async def snapshot_workspace_state(self) -> Mapping[str, object] | None:
        return None

    async def snapshot_session_state(self) -> Mapping[str, object] | None:
        return {
            "messages": [_serialize_message(item) for item in self._messages]
        }

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

    def _commit_task_messages(
        self,
        state: BlackboardTaskState,
        messages: Sequence[Message],
    ) -> None:
        if state.history_committed or not messages:
            return
        self._messages.extend(messages)
        state.history_committed = True

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
                    "url": part.url,
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
            content.append(
                ImagePart(
                    str(part.get("url", "")),
                    (
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
