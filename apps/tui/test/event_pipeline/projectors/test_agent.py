from apps.agent.src.agent_orchestration.capability import (
    AgentCompletedEvent,
    AgentResponse,
    AgentTextDeltaEvent,
    AgentToolCompletedEvent,
    AgentToolStartedEvent,
)
from apps.agent.src.agent_orchestration.events import TaskErrorEvent
from apps.agent.src.agent_orchestration.events import Event
from apps.agent.src.agent_orchestration.tools import ToolExecutionResult
from apps.agent.src.model_provider.types import Message, TextPart, ToolCall
from apps.tui.src.event_pipeline.actions import (
    AppendAssistantDelta,
    AppendError,
    AppendToolStarted,
    UpdateToolCompleted,
)
from apps.tui.src.event_pipeline.projectors.agent import AgentProjector


def test_agent_projector映射文本和稳定工具参数():
    projector = AgentProjector()
    tool_call = ToolCall(
        id="call-1",
        name="read",
        arguments={"z": "你好", "a": 1},
    )

    assert projector.project(
        AgentTextDeltaEvent(
            task_id="task-1", step=1, text="检查中"
        )
    ) == (AppendAssistantDelta(task_id="task-1", text="检查中"),)
    assert projector.project(
        AgentToolStartedEvent(
            task_id="task-1",
            step=1,
            tool_call=tool_call,
        )
    ) == (
        AppendToolStarted(
            task_id="task-1",
            call_id="call-1",
            tool_name="read",
            arguments_json='{"a":1,"z":"你好"}',
        ),
    )


def test_agent_projector工具完成不暴露完整output():
    projector = AgentProjector()
    tool_call = ToolCall(id="call-1", name="bash", arguments={})

    success = projector.project(
        AgentToolCompletedEvent(
            task_id="task-1",
            step=1,
            tool_call=tool_call,
            result=ToolExecutionResult(
                success=True, output="secret output must not escape"
            ),
        )
    )
    failure = projector.project(
        AgentToolCompletedEvent(
            task_id="task-1",
            step=1,
            tool_call=tool_call,
            result=ToolExecutionResult(success=False, error="exit code 1"),
        )
    )

    assert success == (
        UpdateToolCompleted(
            task_id="task-1",
            call_id="call-1",
            tool_name="bash",
            success=True,
            error=None,
        ),
    )
    assert failure == (
        UpdateToolCompleted(
            task_id="task-1",
            call_id="call-1",
            tool_name="bash",
            success=False,
            error="exit code 1",
        ),
    )
    assert "secret output" not in repr(success)


def test_agent_projector映射错误并有意忽略completed与空delta():
    projector = AgentProjector()
    response = AgentResponse(
        message=Message("assistant", [TextPart("done")])
    )

    assert projector.project(
        TaskErrorEvent(
            task_id="task-1",
            fatal=True,
            code="agent_run_failed",
            step=1,
            error_type="RuntimeError",
            error_message="broken",
        )
    ) == (
        AppendError(
            task_id="task-1",
            error_type="RuntimeError",
            message="broken",
        ),
    )
    assert projector.project(
        AgentCompletedEvent(
            task_id="task-1", step=1, response=response
        )
    ) == ()
    assert projector.project(
        AgentTextDeltaEvent(task_id="task-1", step=1, text="")
    ) == ()
    assert projector.project(Event(task_id="task-1")) is None


def test_agent_projector不重复显示tool失败错误事件():
    projector = AgentProjector()

    assert projector.project(
        TaskErrorEvent(
            task_id="task-1",
            fatal=False,
            code="tool_execution_failed",
            error_type="ToolExecutionError",
            error_message="failed",
        )
    ) == ()
