"""BlackboardPlugin 的任务上下文状态。"""

from dataclasses import dataclass, field
from typing import Literal

from apps.agent.src.agent_orchestration.capability import AgentResponse
from apps.agent.src.agent_orchestration.plugins.contracts.events import (
    ContextContributionEvent,
    UserInputEvent,
)


@dataclass
class BlackboardTaskState:
    correlation_id: str
    user_input: UserInputEvent | None = None
    contributions: dict[str, ContextContributionEvent] = field(
        default_factory=dict
    )
    context_published: bool = False
    agent_status: Literal[
        "waiting_context",
        "running",
        "completed",
        "failed",
    ] = "waiting_context"
    agent_response: AgentResponse | None = None
    agent_error: str | None = None

    def is_context_ready(self, required_sources: frozenset[str]) -> bool:
        return (
            self.user_input is not None
            and required_sources.issubset(self.contributions)
        )
