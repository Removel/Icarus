"""Explicit offline demonstration data for native TUI visual acceptance."""

from datetime import UTC, datetime, timedelta

from apps.tui.src.tools.replay import ReplayScenario, ReplayTurn
from packages.gateway_protocol import RuntimeUpdateModel


def preview_scenario() -> ReplayScenario:
    records = []

    def add(kind, payload):
        records.append(
            RuntimeUpdateModel(
                workspace_key="preview",
                session_id="preview-session",
                task_id="preview-task",
                type=kind,
                payload=payload,
                occurred_at=datetime(2026, 1, 1, tzinfo=UTC)
                + timedelta(seconds=len(records)),
            )
        )

    add("task.accepted", {"queue_position": 0})
    add("task.started", {})
    add(
        "assistant.text_delta",
        {
            "step": 1,
            "text": "我会先梳理页面、状态和外部依赖，再把聊天布局与业务流程分开。",
        },
    )
    add(
        "assistant.message",
        {
            "step": 1,
            "text": "我会先梳理页面、状态和外部依赖，再把聊天布局与业务流程分开。现有的流式 Markdown、历史窗口和轮次定位继续复用。",
        },
    )
    add(
        "assistant.thinking_delta",
        {
            "step": 2,
            "text": "这是离线演示数据，不是模型实际推理。先确认状态拥有者和异步结果顺序，再整理组件与样式。",
        },
    )
    for index, (name, path) in enumerate(
        [
            ("read_file", "screens/chat/screen.py"),
            ("list_directory", "widgets/chat/"),
            ("search", "styles/"),
            ("inspect", "client/gateway/"),
            ("validate", "stores/chat.py"),
            ("inspect", "services/chat/"),
        ]
    ):
        add(
            "tool.started",
            {
                "step": 2,
                "call_id": f"preview-{index}",
                "tool_name": name,
                "arguments": {"path": path},
            },
        )
        add(
            "tool.completed",
            {
                "step": 2,
                "call_id": f"preview-{index}",
                "tool_name": name,
                "success": True,
                "error": None,
                "output_preview": {"summary": "离线示例，未执行真实工具", "path": path},
                "preview_truncated": False,
                "full_result_available": False,
            },
        )
    add(
        "assistant.thinking",
        {
            "step": 2,
            "text": "这是离线演示数据。状态、网络、页面和组件已按明确职责组织；界面仍须用真实 Textual 渲染检查。",
            "partial": False,
        },
    )
    add(
        "user.correction",
        {
            "text": "client 按服务对象分类，样式也按页面和组件拆开。",
            "applied_before_step": 3,
        },
    )
    add(
        "assistant.thinking_delta",
        {
            "step": 3,
            "text": "最新 Thinking（离线示例）：执行记录保持固定大小，上一段思考已收进记录。AI 正文始终完整显示。",
        },
    )
    add(
        "assistant.thinking",
        {
            "step": 3,
            "text": "最新 Thinking（离线示例）：执行记录保持固定大小，上一段思考已收进记录。AI 正文始终完整显示。",
            "partial": False,
        },
    )
    text = "可以。接下来按这些职责继续维护：\n\n- client/gateway/ 只负责公共网络协议。\n- Store 保存事实，ViewModel 计算显示值。\n- 页面切换不会结束当前对话。\n- 输入 / 可查看当前已实现的命令。\n\n这是一段离线预览，不连接 Gateway、不调用模型。"
    add("assistant.text_delta", {"step": 3, "text": text})
    add("assistant.message", {"step": 3, "text": text})
    add("task.finished", {"status": "completed", "run_id": "preview-run"})
    return ReplayScenario((ReplayTurn("preview-task", tuple(records)),))
