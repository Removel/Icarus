from apps.agent.src.agent_orchestration.events import TaskErrorEvent
from apps.agent.src.agent_orchestration.plugins.blackboard import (
    BlackboardCompactedEvent,
)
from apps.tui.src.event_pipeline.actions import AppendError
from apps.tui.src.event_pipeline.projectors.blackboard import (
    BlackboardProjector,
)


def test_blackboard_projector映射错误并忽略compact展示():
    projector = BlackboardProjector()

    assert projector.project(
        TaskErrorEvent(
            task_id="task-1",
            fatal=True,
            code="compact_failed",
            error_type="RuntimeError",
            error_message="compact failed",
        )
    ) == (
        AppendError(
            task_id="task-1",
            error_type="RuntimeError",
            message="compact failed",
        ),
    )
    assert projector.project(
        BlackboardCompactedEvent(
            task_id="task-1",
            before_tokens=90,
            after_tokens=10,
        )
    ) == ()
