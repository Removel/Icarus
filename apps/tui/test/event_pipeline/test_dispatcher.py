from apps.agent.src.agent_orchestration.capability import AgentTextDeltaEvent
from apps.agent.src.agent_orchestration.events import Event
from apps.tui.src.event_pipeline import (
    AppendAssistantDelta,
    ProjectorRegistry,
    create_default_projector_registry,
)


class SameEventProjector:
    def project(self, event):
        return ()


def test_default_registry只显式注册当前公开来源():
    registry = create_default_projector_registry()

    assert registry.source_plugin_ids == frozenset(
        {"agent", "blackboard", "user-input"}
    )


def test_dispatcher同时按来源和当前task过滤():
    registry = create_default_projector_registry()
    event = AgentTextDeltaEvent(
        task_id="task-1",
        step=1,
        text="hello",
    )

    assert registry.project("agent", event, active_task_id="task-1") == (
        AppendAssistantDelta(task_id="task-1", text="hello"),
    )
    assert registry.project("user-input", event, active_task_id="task-1") == ()
    assert registry.project("agent", event, active_task_id="other") == ()
    assert registry.project("agent", event, active_task_id=None) == ()

    assert registry.unknown_event_count == 1
    assert registry.unrelated_event_count == 2


def test未知来源和未知event只诊断不显示repr():
    registry = create_default_projector_registry()
    event = Event(task_id="task-1")

    assert registry.project("memory", event, active_task_id="task-1") == ()
    assert registry.project("agent", event, active_task_id="task-1") == ()
    assert registry.unknown_source_count == 1
    assert registry.unknown_event_count == 1


def test_registry拒绝重复或空来源注册():
    registry = ProjectorRegistry()
    registry.register("agent", SameEventProjector())

    import pytest

    with pytest.raises(ValueError, match="already registered"):
        registry.register("agent", SameEventProjector())
    with pytest.raises(ValueError, match="cannot be empty"):
        registry.register("  ", SameEventProjector())
