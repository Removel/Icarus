from packages.gateway_protocol import RuntimeUpdateModel
from apps.tui.src.event_pipeline.projectors.blackboard import BlackboardProjector


def update(update_type):
    return RuntimeUpdateModel(
        workspace_key="workspace", session_id="session", task_id=None,
        type=update_type, payload={}, occurred_at="2026-01-01T00:00:00Z",
    )


def test_blackboard_projector有意识忽略compact与session_lifecycle展示():
    projector = BlackboardProjector()
    assert projector.project(update("context.compacted")) == ()
    assert projector.project(update("session.lifecycle")) == ()
    assert projector.project(update("unknown")) is None
