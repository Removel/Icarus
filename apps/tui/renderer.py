"""REPL Event renderer."""

import json
from typing import TextIO

from apps.agent.src.agent_orchestration.capability import (
    AgentErrorEvent,
    AgentTextDeltaEvent,
    AgentToolCompletedEvent,
    AgentToolStartedEvent,
)
from apps.agent.src.agent_orchestration.events import Event
from apps.agent.src.agent_orchestration.plugins import (
    InputFinishedEvent,
    InputQueuedEvent,
)


class ReplRenderer:
    def __init__(self, output: TextIO) -> None:
        self.output = output
        self._streaming = False

    def render(self, event: Event) -> None:
        if isinstance(event, AgentTextDeltaEvent):
            self.output.write(event.text)
            self.output.flush()
            self._streaming = True
            return

        if isinstance(event, AgentToolStartedEvent):
            self._ensure_newline()
            arguments = json.dumps(
                event.tool_call.arguments,
                ensure_ascii=False,
                default=str,
            )
            self.output.write(
                f"[tool] {event.tool_call.name} {arguments}\n"
            )
            self.output.flush()
            return

        if isinstance(event, AgentToolCompletedEvent):
            self._ensure_newline()
            status = "success" if event.result.success else "failed"
            self.output.write(
                f"[tool] {event.tool_call.name} completed: {status}\n"
            )
            if not event.result.success and event.result.error:
                self.output.write(f"[tool-error] {event.result.error}\n")
            self.output.flush()
            return

        if isinstance(event, InputQueuedEvent):
            self.output.write(
                f"[queue] task accepted, position={event.queue_position}\n"
            )
            self.output.flush()
            return

        if isinstance(event, AgentErrorEvent):
            self._ensure_newline()
            self.output.write(
                f"[error] {event.error_type}: {event.error_message}\n"
            )
            self.output.flush()
            return

        if isinstance(event, InputFinishedEvent):
            self._ensure_newline()
            if event.status == "failed":
                self.output.write("[task] failed\n")
            self.output.flush()

    def finish_turn(self) -> None:
        self._ensure_newline()

    def _ensure_newline(self) -> None:
        if self._streaming:
            self.output.write("\n")
            self._streaming = False
