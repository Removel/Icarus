import pytest

from packages.gateway_protocol import RuntimeUpdateModel
from apps.tui.src.event_pipeline import (
    AppendAssistantDelta,
    ProjectorRegistry,
    create_default_projector_registry,
)


def update(update_type="assistant.text_delta", task_id="task-1"):
    return RuntimeUpdateModel(
        workspace_key="workspace", session_id="session", task_id=task_id,
        type=update_type, payload={"step": 1, "text": "hello"},
        occurred_at="2026-01-01T00:00:00Z",
    )


class SameProjector:
    def project(self, value):
        del value
        return ()


def test_default_registry只显式注册公共update类型():
    registry = create_default_projector_registry()
    assert {
        "user.message",
        "assistant.text_delta",
        "task.finished",
        "session.lifecycle",
    } <= registry.update_types


def test_dispatcher按type和当前task过滤():
    registry = create_default_projector_registry()
    value = update()
    assert registry.project(value, active_task_id="task-1") == (
        AppendAssistantDelta("task-1", "hello"),
    )
    assert registry.project(value, active_task_id="other") == ()
    assert registry.project(value, active_task_id=None) == ()
    assert registry.unrelated_update_count == 2
    assert registry.project(
        value, active_task_id=None, include_unrelated=True
    ) == (AppendAssistantDelta("task-1", "hello"),)


def test未知update只诊断且registry拒绝重复或空type():
    registry = create_default_projector_registry()
    assert registry.project(update("unknown"), active_task_id="task-1") == ()
    assert registry.unknown_update_count == 1
    custom = ProjectorRegistry()
    custom.register("x", SameProjector())
    with pytest.raises(ValueError, match="already registered"):
        custom.register("x", SameProjector())
    with pytest.raises(ValueError, match="cannot be empty"):
        custom.register("  ", SameProjector())
