import asyncio
import json

from apps.agent.src.agent_orchestration.capability import (
    AgentCompletedEvent,
    AgentTextDeltaEvent,
    AgentToolCompletedEvent,
    AgentToolStartedEvent,
)
from apps.agent.src.agent_orchestration.events import Event, TaskErrorEvent
from apps.agent.src.agent_orchestration.plugins.blackboard import (
    BlackboardCompactedEvent,
)
from apps.agent.src.agent_orchestration.plugins.runtime_update import (
    RuntimeUpdatePlugin,
)
from apps.agent.src.agent_orchestration.plugins.user_input import (
    InputFinishedEvent,
    InputQueuedEvent,
    InputStartedEvent,
)
from apps.agent.src.agent_orchestration.tools import ToolExecutionResult
from apps.agent.src.model_provider.types import (
    Message,
    TextPart,
    ToolCall,
    Usage,
)
from apps.agent.src.agent_orchestration.capability import AgentResponse


def project(events):
    async def run():
        updates = []

        async def publish(update):
            updates.append(update)

        plugin = RuntimeUpdatePlugin(
            "runtime-update",
            workspace_key="workspace",
            session_id="session",
            publish_update=publish,
        )
        for source, event in events:
            await plugin.consume(source, event)
        return updates

    return asyncio.run(run())


def test_runtime_update_plugin投影首期公共事件并保留时间顺序():
    call = ToolCall(id="call-1", name="read", arguments={"path": "a"})
    completed = AgentCompletedEvent(
        task_id="task",
        step=2,
        response=AgentResponse(
            Message("assistant", [TextPart("done")]),
            usage=Usage(10, 3),
        ),
    )
    finished = InputFinishedEvent(
        task_id="task", status="completed", run_id="run"
    )
    updates = project(
        [
            ("user-input", InputQueuedEvent(task_id="task", queue_position=0)),
            ("user-input", InputStartedEvent(task_id="task")),
            ("agent", AgentTextDeltaEvent(task_id="task", step=1, text="hi")),
            ("agent", AgentToolStartedEvent(task_id="task", step=1, tool_call=call)),
            (
                "agent",
                AgentToolCompletedEvent(
                    task_id="task",
                    step=1,
                    tool_call=call,
                    result=ToolExecutionResult(success=True, output="ok"),
                ),
            ),
            ("blackboard", BlackboardCompactedEvent(task_id="task", before_tokens=90, after_tokens=10)),
            ("agent", completed),
            ("user-input", finished),
        ]
    )

    assert [update.type for update in updates] == [
        "task.accepted",
        "task.started",
        "assistant.text_delta",
        "tool.started",
        "tool.completed",
        "context.compacted",
        "task.usage",
        "task.finished",
    ]
    assert updates[3].payload["arguments"] == {"path": "a"}
    assert updates[6].payload == {
        "input_tokens": 10,
        "output_tokens": 3,
        "total_tokens": 13,
    }
    assert updates[6].occurred_at == completed.occurred_at
    assert updates[7].occurred_at == finished.occurred_at
    json.dumps(dict(updates[3].payload))


def test_runtime_update_plugin过滤内部事件空delta和重复tool错误():
    updates = project(
        [
            ("internal", Event(task_id="task")),
            ("agent", AgentTextDeltaEvent(task_id="task", step=1, text="")),
            (
                "agent",
                TaskErrorEvent(
                    task_id="task",
                    fatal=False,
                    code="tool_execution_failed",
                    error_type="ToolError",
                    error_message="already shown",
                ),
            ),
        ]
    )

    assert updates == []


def test_runtime_update_plugin投影安全task错误且不暴露内部消息():
    updates = project(
        [
            (
                "blackboard",
                TaskErrorEvent(
                    task_id="task",
                    fatal=True,
                    code="context_failed",
                    error_type="ContextError",
                    error_message="safe",
                    step=2,
                    run_id="run",
                    task_messages=(Message("user", [TextPart("secret")]),),
                    last_usage=Usage(5, 1),
                ),
            )
        ]
    )

    assert len(updates) == 1
    assert updates[0].type == "task.error"
    assert updates[0].payload == {
        "fatal": True,
        "code": "context_failed",
        "error_type": "ContextError",
        "message": "safe",
        "step": 2,
        "run_id": "run",
    }
    assert "secret" not in repr(updates[0])


def test_runtime_update拒绝非json_payload():
    event = InputQueuedEvent(task_id="task", queue_position=0)
    updates = project([("user-input", event)])
    assert updates[0].workspace_key == "workspace"
    assert updates[0].session_id == "session"
