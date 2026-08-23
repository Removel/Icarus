"""将 HookEvent 快速送入 Trace Writer。"""

import logging
from pathlib import Path

from apps.agent.src.agent_orchestration.hooks.base_hook import BaseHook
from apps.agent.src.agent_orchestration.hooks.hook_event import HookEvent
from apps.agent.src.agent_orchestration.plugins.persistence.redactor import Redactor
from apps.agent.src.agent_orchestration.plugins.persistence.session_identity import (
    SessionIdentity,
)
from apps.agent.src.agent_orchestration.plugins.persistence.trace_record import (
    TraceRecord,
)
from apps.agent.src.agent_orchestration.plugins.persistence.trace_writer import (
    FileTraceWriter,
    TraceWriteRequest,
)


logger = logging.getLogger(__name__)


class FileTraceHook(BaseHook):
    def __init__(
        self,
        writer: FileTraceWriter,
        redactor: Redactor,
    ) -> None:
        self.writer = writer
        self.redactor = redactor
        self.skipped_count = 0

    def handle(self, event: HookEvent) -> None:
        self._offer(event)

    async def ahandle(self, event: HookEvent) -> None:
        self._offer(event)

    def _offer(self, event: HookEvent) -> None:
        identity = self._identity_from_event(event)
        if identity is None:
            self.skipped_count += 1
            logger.warning(
                "Skip trace event without workspace/session context: event_id=%s",
                event.event_id,
            )
            return
        record = TraceRecord.from_hook_event(event, self.redactor)
        self.writer.offer(TraceWriteRequest(identity=identity, record=record))

    @staticmethod
    def _identity_from_event(event: HookEvent) -> SessionIdentity | None:
        workspace_path = event.context.get("workspace_path")
        workspace_key = event.context.get("workspace_key")
        session_id = event.context.get("session_id")
        if not workspace_path or not workspace_key or not session_id:
            return None
        return SessionIdentity(
            workspace_path=Path(str(workspace_path)),
            workspace_key=str(workspace_key),
            session_id=str(session_id),
        )
