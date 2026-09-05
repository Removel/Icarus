"""Project internal Plugin Events into stable application updates."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from apps.agent.src.agent_orchestration.capability import (
    AgentCompletedEvent,
    AgentMessageCompletedEvent,
    AgentTextDeltaEvent,
    AgentToolCompletedEvent,
    AgentToolStartedEvent,
)
from apps.agent.src.agent_orchestration.events import Event, TaskErrorEvent
from apps.agent.src.agent_orchestration.plugin_runtime import BasePlugin
from apps.agent.src.agent_orchestration.plugins.blackboard import (
    BlackboardCompactedEvent,
)
from apps.agent.src.agent_orchestration.plugins.user_input import (
    InputFinishedEvent,
    InputQueuedEvent,
    InputStartedEvent,
)
from apps.agent.src.agent_orchestration.plugins.persistence.redactor import Redactor
from apps.agent.src.model_provider.types import TextPart
from apps.agent.src.runtime_update import RuntimeUpdate


UpdatePublisher = Callable[[RuntimeUpdate], Awaitable[None]]


class RuntimeUpdatePlugin(BasePlugin):
    def __init__(
        self,
        plugin_id: str,
        *,
        workspace_key: str,
        session_id: str,
        publish_update: UpdatePublisher,
        redactor: Redactor | None = None,
    ) -> None:
        super().__init__(plugin_id)
        self.workspace_key = workspace_key
        self.session_id = session_id
        self._publish_update = publish_update
        self._redactor = redactor or Redactor()

    async def consume(self, source_plugin_id: str, event: Event) -> None:
        update = self._project(source_plugin_id, event)
        if update is not None:
            await self._publish_update(update)

    def _project(
        self, source_plugin_id: str, event: Event
    ) -> RuntimeUpdate | None:
        task_id = event.task_id
        update_type = None
        payload: dict[str, object] = {}
        if isinstance(event, InputQueuedEvent):
            update_type = "task.accepted"
            payload = {"queue_position": event.queue_position}
        elif isinstance(event, InputStartedEvent):
            update_type = "task.started"
        elif isinstance(event, InputFinishedEvent):
            update_type = "task.finished"
            payload = {"status": event.status, "run_id": event.run_id}
        elif isinstance(event, AgentTextDeltaEvent):
            if not event.text:
                return None
            update_type = "assistant.text_delta"
            payload = {"step": event.step, "text": event.text}
        elif isinstance(event, AgentMessageCompletedEvent):
            text = "".join(
                part.text
                for part in event.message.content
                if isinstance(part, TextPart)
            )
            if not text:
                return None
            update_type = "assistant.message"
            payload = {"step": event.step, "text": text}
        elif isinstance(event, AgentToolStartedEvent):
            update_type = "tool.started"
            payload = {
                "step": event.step,
                "call_id": event.tool_call.id,
                "tool_name": event.tool_call.name,
                "arguments": self._redactor.redact(
                    dict(event.tool_call.arguments)
                ),
            }
        elif isinstance(event, AgentToolCompletedEvent):
            update_type = "tool.completed"
            payload = {
                "step": event.step,
                "call_id": event.tool_call.id,
                "tool_name": event.tool_call.name,
                "success": event.result.success,
                "error": (
                    self._redactor.redact_text(event.result.error)
                    if not event.result.success
                    and event.result.error is not None
                    else None
                ),
            }
        elif isinstance(event, AgentCompletedEvent):
            usage = event.response.usage
            if usage is None:
                return None
            update_type = "task.usage"
            payload = {
                "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens,
                "total_tokens": usage.total_tokens,
            }
        elif isinstance(event, TaskErrorEvent):
            if event.code == "tool_execution_failed":
                return None
            update_type = "task.error"
            payload = {
                "fatal": event.fatal,
                "code": event.code,
                "error_type": event.error_type,
                "message": self._redactor.redact_text(
                    event.error_message
                ),
                "step": event.step,
                "run_id": event.run_id,
            }
        elif isinstance(event, BlackboardCompactedEvent):
            update_type = "context.compacted"
            payload = {
                "before_tokens": event.before_tokens,
                "after_tokens": event.after_tokens,
            }
        if update_type is None:
            return None
        del source_plugin_id
        return RuntimeUpdate(
            workspace_key=self.workspace_key,
            session_id=self.session_id,
            task_id=task_id,
            type=update_type,
            payload=payload,
            occurred_at=event.occurred_at,
        )
