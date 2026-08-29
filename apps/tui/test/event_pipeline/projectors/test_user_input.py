from packages.gateway_protocol import RuntimeUpdateModel
from apps.tui.src.event_pipeline.actions import FinishTurn, SetRuntimeStatus
from apps.tui.src.event_pipeline.projectors.user_input import UserInputProjector


def update(update_type, payload=None, task_id="task-1"):
    return RuntimeUpdateModel(
        workspace_key="workspace", session_id="session", task_id=task_id,
        type=update_type, payload=payload or {}, occurred_at="2026-01-01T00:00:00Z",
    )


def test_user_input_projector映射生命周期():
    projector = UserInputProjector()
    assert projector.project(update("task.accepted", {"queue_position": 0})) == (
        SetRuntimeStatus("task-1", "accepted", "Accepted by runtime"),
    )
    assert projector.project(update("task.started")) == (
        SetRuntimeStatus("task-1", "running", "Agent is running"),
    )
    assert projector.project(update("task.finished", {"status": "completed"})) == (
        FinishTurn("task-1", "completed"),
    )
    assert projector.project(update("task.finished", {"status": "cancelled"})) == (
        FinishTurn("task-1", "cancelled"),
    )
    assert projector.project(update("unknown")) is None
