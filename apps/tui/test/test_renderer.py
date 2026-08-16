from io import StringIO

from apps.agent.src.agent_orchestration.capability import (
    AgentErrorEvent,
    AgentTextDeltaEvent,
    AgentToolCompletedEvent,
    AgentToolStartedEvent,
)
from apps.agent.src.agent_orchestration.events import Event
from apps.agent.src.agent_orchestration.plugins import (
    InputFinishedEvent,
    InputQueuedEvent,
)
from apps.agent.src.agent_orchestration.tools import ToolExecutionResult
from apps.agent.src.model_provider.types import ToolCall
from apps.tui.renderer import ReplRenderer


def test_renderer_流式文本与工具事件保持可读换行且不展开完整结果():
    output = StringIO()
    renderer = ReplRenderer(output)
    tool_call = ToolCall(
        id="call-1",
        name="read",
        arguments={"path": "你好.txt"},
    )

    renderer.render(
        AgentTextDeltaEvent(
            correlation_id="task-1",
            step=1,
            text="让我读取",
        )
    )
    renderer.render(
        AgentTextDeltaEvent(
            correlation_id="task-1",
            step=1,
            text="文件。",
        )
    )
    renderer.render(
        AgentToolStartedEvent(
            correlation_id="task-1",
            step=1,
            tool_call=tool_call,
        )
    )
    renderer.render(
        AgentToolCompletedEvent(
            correlation_id="task-1",
            step=1,
            tool_call=tool_call,
            result=ToolExecutionResult(
                success=True,
                output="不应展示的完整工具结果",
            ),
        )
    )
    renderer.finish_turn()

    assert output.getvalue() == (
        "让我读取文件。\n"
        '[tool] read {"path": "你好.txt"}\n'
        "[tool] read completed: success\n"
    )
    assert "不应展示的完整工具结果" not in output.getvalue()


def test_renderer_展示队列工具失败和agent错误():
    output = StringIO()
    renderer = ReplRenderer(output)
    tool_call = ToolCall(id="call-1", name="bash", arguments={"command": "false"})

    renderer.render(
        InputQueuedEvent(
            correlation_id="task-1",
            task_id="task-1",
            queue_position=2,
        )
    )
    renderer.render(
        AgentToolCompletedEvent(
            correlation_id="task-1",
            step=1,
            tool_call=tool_call,
            result=ToolExecutionResult(
                success=False,
                error="exit code 1",
            ),
        )
    )
    renderer.render(
        AgentErrorEvent(
            correlation_id="task-1",
            step=1,
            error_type="RuntimeError",
            error_message="agent failed",
        )
    )
    renderer.render(
        InputFinishedEvent(
            correlation_id="task-1",
            task_id="task-1",
            status="failed",
        )
    )

    assert output.getvalue() == (
        "[queue] task accepted, position=2\n"
        "[tool] bash completed: failed\n"
        "[tool-error] exit code 1\n"
        "[error] RuntimeError: agent failed\n"
        "[task] failed\n"
    )


def test_renderer_忽略未知事件():
    output = StringIO()
    renderer = ReplRenderer(output)

    renderer.render(Event(correlation_id="task-1"))

    assert output.getvalue() == ""
