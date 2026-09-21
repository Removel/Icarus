import asyncio
from datetime import UTC, datetime

import pytest

from apps.agent.src.agent_orchestration.plugins.user_input import InputAccepted
from apps.agent.src.agent_orchestration.run_control import TaskOperationResult
from apps.agent.src.application import DiscardSessionResult, SessionSummary
from apps.gateway.src.protocol.errors import BUSINESS_ERROR, GatewayRpcError
from apps.gateway.src.protocol.methods import GatewayMethods
from apps.agent.src.runtime_update import RuntimeUpdate


class RuntimeStub:
    is_running = True

    async def create_session(self, workspace_path, session_id=None):
        self.created = (workspace_path, session_id)
        return session_id or "generated"

    async def get_session_status(self, workspace_path, session_id):
        return {
            "workspace_key": "workspace",
            "session_id": session_id,
            "lifecycle": "ready",
        }

    async def list_session_summaries(self, workspace_path):
        del workspace_path
        return (SessionSummary("session", "first message"),)

    async def discard_empty_session(self, workspace_path, session_id):
        self.discarded = (workspace_path, session_id)
        return DiscardSessionResult(
            "workspace", session_id, "discarded"
        )

    async def submit(
        self,
        workspace_path,
        session_id,
        prompt,
        *,
        submission_id,
        resources,
        display_text=None,
    ):
        self.submitted = (
            workspace_path,
            session_id,
            prompt,
            submission_id,
            resources,
            display_text,
        )
        return InputAccepted("task", 0)

    async def get_session_history(
        self, workspace_path, session_id, *, after_sequence=0
    ):
        del workspace_path
        return (
            (
                RuntimeUpdate(
                    workspace_key="workspace",
                    session_id=session_id,
                    task_id="task",
                    type="user.message",
                    payload={"text": "hello", "resources": []},
                    occurred_at=datetime(2026, 1, 1, tzinfo=UTC),
                    sequence=1,
                ),
                RuntimeUpdate(
                    workspace_key="workspace",
                    session_id=session_id,
                    task_id="task",
                    type="assistant.thinking",
                    payload={
                        "step": 1,
                        "text": "considered the request",
                        "partial": False,
                    },
                    occurred_at=datetime(2026, 1, 1, tzinfo=UTC),
                    sequence=2,
                ),
                RuntimeUpdate(
                    workspace_key="workspace",
                    session_id=session_id,
                    task_id="task",
                    type="tool.completed",
                    payload={
                        "step": 1,
                        "call_id": "call-1",
                        "tool_name": "read",
                        "success": True,
                        "error": None,
                        "output_preview": {"count": 1},
                        "preview_truncated": False,
                        "full_result_available": False,
                        "preview_error": None,
                    },
                    occurred_at=datetime(2026, 1, 1, tzinfo=UTC),
                    sequence=3,
                ),
            )
            if after_sequence < 3
            else (),
            3,
        )

    async def cancel_task(self, *args):
        raise AssertionError(args)

    async def steer_task(
        self,
        workspace_path,
        session_id,
        task_id,
        prompt,
        *,
        resources,
        display_text=None,
        submission_id=None,
    ):
        self.steered = (
            workspace_path,
            session_id,
            task_id,
            prompt,
            resources,
            display_text,
            submission_id,
        )
        return TaskOperationResult(task_id=task_id, status="accepted", run_id="run")

    async def unload_session(self, *args):
        raise AssertionError(args)

    def get_task_status(self, *args):
        raise KeyError(args)


def test_gateway_methods显式适配create_submit和connection订阅():
    async def run():
        runtime = RuntimeStub()
        methods = GatewayMethods(runtime)
        subscriptions = set()
        created = await methods.dispatch(
            "session.create",
            {"workspace_path": "/workspace", "session_id": "session"},
            subscriptions,
        )
        accepted = await methods.dispatch(
            "session.submit",
            {
                "workspace_path": "/workspace",
                "session_id": "session",
                "prompt": "hello",
                "display_text": "visible hello",
                "submission_id": "submission",
                "resources": [
                    {"resource_id": "client/image.png", "media_type": "image/png"}
                ],
            },
            subscriptions,
        )
        subscribed = await methods.dispatch(
            "session.subscribe",
            {"workspace_key": "workspace", "session_id": "session"},
            subscriptions,
        )
        return runtime, created, accepted, subscribed, subscriptions

    runtime, created, accepted, subscribed, subscriptions = asyncio.run(run())
    assert created["session_id"] == "session"
    assert accepted == {"task_id": "task", "queue_position": 0}
    assert runtime.submitted[3] == "submission"
    assert runtime.submitted[4][0].resource_id == "client/image.png"
    assert runtime.submitted[5] == "visible hello"
    assert subscribed == {"subscribed": True}
    assert subscriptions == {("workspace", "session")}


def test_gateway_methods读取session历史():
    async def run():
        return await GatewayMethods(RuntimeStub()).dispatch(
            "session.get_history",
            {
                "workspace_path": "/workspace",
                "session_id": "session",
                "after_sequence": 0,
            },
            set(),
        )

    result = asyncio.run(run())
    assert result["history_cursor"] == 3
    assert result["next_after_sequence"] == 3
    assert result["has_more"] is False
    assert result["records"][0]["type"] == "user.message"
    assert result["records"][0]["sequence"] == 1
    assert result["records"][1]["type"] == "assistant.thinking"
    assert result["records"][1]["payload"] == {
        "step": 1,
        "text": "considered the request",
        "partial": False,
    }
    assert result["records"][2]["payload"]["output_preview"] == {
        "count": 1
    }


def test_gateway_methods将文本和图片steer路由到当前task():
    async def run():
        runtime = RuntimeStub()
        result = await GatewayMethods(runtime).dispatch(
            "session.steer",
            {
                "workspace_path": "/workspace",
                "session_id": "session",
                "task_id": "task",
                "prompt": "look at the image",
                "display_text": "look [#image1]",
                "submission_id": "steer-1",
                "resources": [
                    {"resource_id": "client/image.png", "media_type": "image/png"}
                ],
            },
            set(),
        )
        return runtime, result

    runtime, result = asyncio.run(run())

    assert result == {"task_id": "task", "status": "accepted", "run_id": "run"}
    assert runtime.steered[0:4] == (
        "/workspace",
        "session",
        "task",
        "look at the image",
    )
    assert runtime.steered[4][0].resource_id == "client/image.png"
    assert runtime.steered[5] == "look [#image1]"
    assert runtime.steered[6] == "steer-1"


def test_gateway_methods兼容省略steer_submission_id并拒绝空值():
    async def run():
        runtime = RuntimeStub()
        methods = GatewayMethods(runtime)
        result = await methods.dispatch(
            "session.steer",
            {
                "workspace_path": "/workspace",
                "session_id": "session",
                "task_id": "task",
                "prompt": "continue",
            },
            set(),
        )
        with pytest.raises(GatewayRpcError) as invalid:
            await methods.dispatch(
                "session.steer",
                {
                    "workspace_path": "/workspace",
                    "session_id": "session",
                    "task_id": "task",
                    "prompt": "continue",
                    "submission_id": "  ",
                },
                set(),
            )
        return runtime, result, invalid.value

    runtime, result, invalid = asyncio.run(run())

    assert result["status"] == "accepted"
    assert runtime.steered[6] is None
    assert invalid.code == -32602


def test_gateway_methods列出摘要并清理空session():
    async def run():
        runtime = RuntimeStub()
        methods = GatewayMethods(runtime)
        sessions = await methods.dispatch(
            "session.list", {"workspace_path": "/workspace"}, set()
        )
        discarded = await methods.dispatch(
            "session.discard_empty",
            {"workspace_path": "/workspace", "session_id": "session"},
            set(),
        )
        return runtime, sessions, discarded

    runtime, sessions, discarded = asyncio.run(run())
    assert sessions == {
        "sessions": [
            {"session_id": "session", "first_user_input": "first message"}
        ]
    }
    assert discarded == {
        "workspace_key": "workspace",
        "session_id": "session",
        "status": "discarded",
    }
    assert runtime.discarded == ("/workspace", "session")


def test_gateway_methods非法参数和业务错误不泄漏内部异常():
    async def run_invalid():
        methods = GatewayMethods(RuntimeStub())
        with pytest.raises(GatewayRpcError) as invalid:
            await methods.dispatch("session.get", {}, set())
        with pytest.raises(GatewayRpcError) as unavailable:
            await methods.dispatch(
                "task.get_status",
                {
                    "workspace_path": "/workspace",
                    "session_id": "session",
                    "task_id": "missing",
                },
                set(),
            )
        return invalid.value, unavailable.value

    invalid, unavailable = asyncio.run(run_invalid())
    assert invalid.code == -32602
    assert unavailable.code == BUSINESS_ERROR
    assert unavailable.data == {"code": "task_status_unavailable"}
    assert "missing" not in unavailable.message
