from apps.agent.src.agent_orchestration.events import Event, TaskErrorEvent
from apps.agent.src.agent_orchestration.plugins import (
    InputFinishedEvent,
    InputQueuedEvent,
    InputStartedEvent,
    UserInputEvent,
)
from apps.tui.src.event_pipeline.actions import (
    AppendError,
    FinishTurn,
    SetRuntimeStatus,
)
from apps.tui.src.event_pipeline.projectors.user_input import UserInputProjector


def test_user_input_projector映射生命周期但不重复显示用户消息():
    projector = UserInputProjector()

    assert projector.project(
        InputQueuedEvent(
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
        InputStartedEvent(task_id="task-1")
    ) == (
        SetRuntimeStatus(
            task_id="task-1",
            status="running",
            text="Agent is running",
        ),
    )
    assert projector.project(
        UserInputEvent(task_id="task-1", prompt="hello")
    ) == ()
    assert projector.project(
        InputFinishedEvent(
            task_id="task-1",
            status="completed",
        )
    ) == (FinishTurn(task_id="task-1", status="completed"),)


def test_user_input_projector拒绝未知event():
    projector = UserInputProjector()

    assert projector.project(
        InputFinishedEvent(
            task_id="task-2",
            status="failed",
        )
    ) == (FinishTurn(task_id="task-2", status="failed"),)
    assert projector.project(Event(task_id="task-1")) is None


def test_user_input_projector映射取消终态():
    projector = UserInputProjector()

    assert projector.project(
        InputFinishedEvent(task_id="task-1", status="cancelled")
    ) == (FinishTurn(task_id="task-1", status="cancelled"),)


def test_user_input_projector映射输入错误():
    projector = UserInputProjector()
    assert projector.project(
        TaskErrorEvent(
            task_id="task-1",
            fatal=True,
            code="image_import_failed",
            error_type="ImageAssetError",
            error_message="image file is unavailable",
        )
    ) == (
        AppendError(
            task_id="task-1",
            error_type="ImageAssetError",
            message="image file is unavailable",
        ),
    )
