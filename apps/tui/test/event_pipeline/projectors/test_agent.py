from packages.gateway_protocol import RuntimeUpdateModel
from apps.tui.src.event_pipeline.actions import (
    AppendAssistantDelta,
    AppendThinkingDelta,
    AppendError,
    AppendToolStarted,
    CompleteAssistantMessage,
    CompleteThinking,
    UpdateToolCompleted,
)
from apps.tui.src.event_pipeline.projectors.agent import AgentProjector


def update(update_type, payload, task_id="task-1"):
    return RuntimeUpdateModel(
        workspace_key="workspace",
        session_id="session",
        task_id=task_id,
        type=update_type,
        payload=payload,
        occurred_at="2026-01-01T00:00:00Z",
    )


def test_agent_projector映射文本和稳定工具参数():
    projector = AgentProjector()
    assert projector.project(
        update("assistant.text_delta", {"step": 1, "text": "检查中"})
    ) == (AppendAssistantDelta(task_id="task-1", text="检查中"),)
    assert projector.project(
        update("assistant.message", {"step": 1, "text": "检查完成"})
    ) == (
        CompleteAssistantMessage(task_id="task-1", text="检查完成"),
    )
    assert projector.project(
        update(
            "tool.started",
            {
                "step": 1, "call_id": "call-1",
                "tool_name": "read", "arguments": {"z": "你好", "a": 1},
            },
        )
    ) == (
        AppendToolStarted(
            task_id="task-1", call_id="call-1", tool_name="read",
            arguments_json='{"a":1,"z":"你好"}',
        ),
    )


def test_agent_projector工具完成不暴露完整output():
    projector = AgentProjector()
    success = projector.project(
        update(
            "tool.completed",
            {
                "step": 1, "call_id": "call-1", "tool_name": "bash",
                "success": True, "error": None,
            },
        )
    )
    failure = projector.project(
        update(
            "tool.completed",
            {
                "step": 1, "call_id": "call-1", "tool_name": "bash",
                "success": False, "error": "exit code 1",
            },
        )
    )
    assert success == (
        UpdateToolCompleted("task-1", "call-1", "bash", True, None),
    )
    assert failure == (
        UpdateToolCompleted("task-1", "call-1", "bash", False, "exit code 1"),
    )
    assert success[0].output_preview is None


def test_agent_projector映射实时和历史thinking():
    projector = AgentProjector()

    assert projector.project(
        update("assistant.thinking_delta", {"step": 2, "text": "分析"})
    ) == (AppendThinkingDelta("task-1", 2, "分析", False),)
    assert projector.project(
        update(
            "assistant.thinking",
            {"step": 2, "text": "分析完成", "partial": True},
        ),
        historical=True,
    ) == (CompleteThinking("task-1", 2, "分析完成", True, True),)


def test_agent_projector映射tool_preview并兼容旧记录():
    projector = AgentProjector()
    projected = projector.project(
        update(
            "tool.completed",
            {
                "step": 3,
                "call_id": "call-1",
                "tool_name": "read",
                "success": True,
                "error": None,
                "output_preview": {"path": "README.md"},
                "preview_truncated": True,
                "full_result_available": True,
                "preview_error": None,
            },
        )
    )

    assert projected == (
        UpdateToolCompleted(
            "task-1",
            "call-1",
            "read",
            True,
            None,
            3,
            {"path": "README.md"},
            True,
            True,
            None,
        ),
    )


def test_agent_projector映射错误并忽略usage和空delta():
    projector = AgentProjector()
    assert projector.project(
        update(
            "task.error",
            {
                "fatal": True, "code": "agent_run_failed",
                "error_type": "RuntimeError", "message": "broken",
                "step": 1, "run_id": None,
            },
        )
    ) == (AppendError("task-1", "RuntimeError", "broken"),)
    assert projector.project(
        update("assistant.text_delta", {"step": 1, "text": ""})
    ) == ()
    assert projector.project(
        update("task.usage", {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2})
    ) == ()
    assert projector.project(update("unknown", {})) is None
