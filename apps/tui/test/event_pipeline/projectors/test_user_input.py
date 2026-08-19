from apps.agent.src.agent_orchestration.events import Event
from apps.agent.src.agent_orchestration.plugins import (
    InputFinishedEvent,
    InputQueuedEvent,
    InputStartedEvent,
    UserInputEvent,
)
from apps.tui.src.event_pipeline.actions import FinishTurn, SetRuntimeStatus
from apps.tui.src.event_pipeline.projectors.user_input import UserInputProjector


def test_user_input_projector映射生命周期但不重复显示用户消息():
    projector = UserInputProjector()

    assert projector.project(
        InputQueuedEvent(
            correlation_id="task-1",
            task_id="task-1",
            queue_position=0,
        )
    ) == (
        SetRuntimeStatus(
            task_id="task-1",
            status="accepted",
            text="Accepted by runtime",
        ),
    )
    assert projector.project(
        InputStartedEvent(correlation_id="task-1", task_id="task-1")
    ) == (
        SetRuntimeStatus(
            task_id="task-1",
            status="running",
            text="Agent is running",
        ),
    )
    assert projector.project(
        UserInputEvent(correlation_id="task-1", prompt="hello")
    ) == ()
    assert projector.project(
        InputFinishedEvent(
            correlation_id="task-1",
            task_id="task-1",
            status="completed",
        )
    ) == (FinishTurn(task_id="task-1", status="completed"),)


def test_user_input_projector拒绝内部task_id不一致和未知event():
    projector = UserInputProjector()

    assert projector.project(
        InputFinishedEvent(
            correlation_id="task-1",
            task_id="task-2",
            status="failed",
        )
    ) == ()
    assert projector.project(Event(correlation_id="task-1")) is None
