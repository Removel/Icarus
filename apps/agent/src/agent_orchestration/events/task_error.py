"""Task 内统一的可观察错误事件。"""

from dataclasses import dataclass

from apps.agent.src.agent_orchestration.events.base_event import Event
from apps.agent.src.model_provider.types import Message, Usage


@dataclass(frozen=True, kw_only=True)
class TaskErrorEvent(Event):
    """表达 Task 内的致命或非致命错误，不携带异常对象。"""

    fatal: bool
    code: str
    error_type: str
    error_message: str
    step: int | None = None
    run_id: str | None = None
    task_messages: tuple[Message, ...] = ()
    last_usage: Usage | None = None
