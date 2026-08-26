"""BlackboardPlugin 的任务上下文状态。"""

from dataclasses import dataclass, field

from apps.agent.src.agent_orchestration.plugins.blackboard.events import (
    ContextContributionEvent,
)
from apps.agent.src.agent_orchestration.plugins.user_input.events import UserInputEvent


@dataclass
class BlackboardTaskState:
    task_id: str
    user_input: UserInputEvent | None = None
    contributions: dict[str, ContextContributionEvent] = field(
        default_factory=dict
    )
    input_prompt: str | None = None
    context_published: bool = False
    history_committed: bool = False
    agent_finished: bool = False
    input_finished: bool = False
    reported_context_errors: set[str] = field(default_factory=set)

    def is_context_ready(self, required_sources: frozenset[str]) -> bool:
        return (
            self.user_input is not None
            and required_sources.issubset(self.contributions)
        )
