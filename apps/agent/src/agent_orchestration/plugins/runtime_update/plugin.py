"""Project internal Plugin Events into stable application updates."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from apps.agent.src.agent_orchestration.capability import (
    AgentCompletedEvent,
    AgentMessageCompletedEvent,
    AgentTextDeltaEvent,
    AgentThinkingCompletedEvent,
    AgentThinkingDeltaEvent,
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
from apps.agent.src.agent_orchestration.run_control import TaskSteerAppliedEvent
from apps.agent.src.agent_orchestration.plugins.persistence.redactor import Redactor
from apps.agent.src.agent_orchestration.plugins.process.events import (
    ProcessUpdatedEvent,
)
from apps.agent.src.agent_orchestration.plugins.runtime_update import tool_preview
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
        elif isinstance(event, AgentThinkingDeltaEvent):
            if not event.text:
                return None
            update_type = "assistant.thinking_delta"
            payload = {"step": event.step, "text": event.text}
        elif isinstance(event, AgentThinkingCompletedEvent):
            if not event.text:
                return None
            update_type = "assistant.thinking"
            payload = {
                "step": event.step,
                "text": event.text,
                "partial": event.partial,
            }
        elif isinstance(event, TaskSteerAppliedEvent):
            update_type = "user.correction"
            payload = {
                "text": (
                    event.content
                    if event.display_text is None
                    else event.display_text
                ),
                "resources": [
                    {
                        "resource_id": image.source,
                        "media_type": image.media_type,
                    }
                    for image in event.input_images
                ],
                "applied_before_step": event.applied_before_step,
            }
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
            full_result_available = tool_preview.full_result_available(
                event.result
            )
            try:
                projection = tool_preview.build_public_tool_projection(
                    event.result, self._redactor
                )
            except Exception:
                try:
                    safe_error = tool_preview.safe_tool_error(
                        event.result, self._redactor
                    )
                except Exception:
                    safe_error = (
                        "Tool execution error unavailable"
                        if event.result.error is not None
                        else None
                    )
                projection = tool_preview.PublicToolProjection(
                    output_preview=None,
                    preview_truncated=True,
                    full_result_available=full_result_available,
                    preview_error=tool_preview.PREVIEW_UNAVAILABLE,
                    safe_error=safe_error,
                )
            update_type = "tool.completed"
            payload = {
                "step": event.step,
                "call_id": event.tool_call.id,
                "tool_name": event.tool_call.name,
                "success": event.result.success,
                "error": (
                    projection.safe_error
                    if not event.result.success
                    else None
                ),
                "output_preview": projection.output_preview,
                "preview_truncated": projection.preview_truncated,
                "full_result_available": (
                    projection.full_result_available
                ),
                "preview_error": projection.preview_error,
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
        elif isinstance(event, ProcessUpdatedEvent):
            update_type = "process.updated"
            payload = {
                "process_id": event.process_id,
                "pid": event.pid,
                "command": self._redactor.redact_text(event.command),
                "workdir": self._redactor.redact_text(event.workdir),
                "status": event.status,
                "started_at": event.started_at.isoformat().replace("+00:00", "Z"),
                "ended_at": (
                    event.ended_at.isoformat().replace("+00:00", "Z")
                    if event.ended_at is not None
                    else None
                ),
                "exit_code": event.exit_code,
                "stop_reason": (
                    self._redactor.redact_text(event.stop_reason)
                    if event.stop_reason is not None
                    else None
                ),
                "log_truncated": event.log_truncated,
                "origin_task_id": event.origin_task_id,
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
