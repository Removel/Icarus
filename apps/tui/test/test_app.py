import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
import threading

import pytest
from textual.widgets import Static

from packages.gateway_protocol import (
    DiscardEmptySessionResultModel,
    RuntimeUpdateModel,
    SessionHistoryModel,
    SessionSummaryModel,
)
from apps.tui.src.gateway_client.models import (
    SubmitAccepted,
    TaskOperationResult,
)
from apps.tui.src.app import IcarusTextualApp, RuntimeSubscriptionFailed
from apps.tui.src.chat_state import RuntimePhase
from apps.tui.src.clipboard import (
    ClipboardImage,
    ClipboardImageReadError,
)
from apps.tui.src.event_pipeline import FinishTurn, ShowNotification
from apps.tui.src.screens import SessionPicker
from apps.tui.src.event_pipeline.dispatcher import ProjectorRegistry
from apps.tui.src.widgets import (
    AssistantMessage,
    ConversationView,
    PersistentComposer,
    QueuePanel,
    RuntimeStatusBar,
)
from apps.tui.src.widgets.messages import (
    ErrorMessage,
    ToolMessage,
    TurnStatusMessage,
    UserMessage,
)


class ControlledSubscription:
    def __init__(self, actions, *, close_error=None) -> None:
        self.actions = actions
        self.queue = asyncio.Queue()
        self.closed = False
        self.close_error = close_error

    async def next_update(self):
        item = await self.queue.get()
        if item is None:
            raise RuntimeError("subscription is closed")
        return item

    def publish(self, source, event) -> None:
        del source
        if not isinstance(event, RuntimeUpdateModel):
            raise TypeError("ControlledSubscription accepts RuntimeUpdateModel")
        self.queue.put_nowait(event)

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        self.actions.append("subscription-close")
        self.queue.put_nowait(None)
        if self.close_error is not None:
            raise self.close_error


class ControlledService:
    def __init__(
        self,
        *,
        block_start: bool = False,
        publish_queued_before_return: bool = False,
        submit_error: BaseException | None = None,
        start_error: BaseException | None = None,
        subscribe_error: BaseException | None = None,
        subscription_close_error: BaseException | None = None,
        stop_error: BaseException | None = None,
        cancel_error: BaseException | None = None,
    ) -> None:
        self.actions = []
        self.subscription = ControlledSubscription(
            self.actions,
            close_error=subscription_close_error,
        )
        self.start_entered = asyncio.Event()
        self.start_release = asyncio.Event()
        if not block_start:
            self.start_release.set()
        self.publish_queued_before_return = publish_queued_before_return
        self.submit_error = submit_error
        self.start_error = start_error
        self.subscribe_error = subscribe_error
        self.stop_error = stop_error
        self.cancel_error = cancel_error
        self.submissions = []
        self.submission_images: list[tuple[Path, ...]] = []
        self.session_id = "test-session"
        self.workspace_key = "workspace"
        self.stopped = False
        self.cancelled_tasks = []
        self.task_statuses = {}
        self.history = SessionHistoryModel(records=(), history_cursor=0)
        self.session_summaries = ()
        self.session_status = {
            "workspace_key": self.workspace_key,
            "session_id": self.session_id,
            "lifecycle": "ready",
        }
        self.discarded_sessions = []

    async def start(self) -> None:
        self.actions.append("service-start")
        self.start_entered.set()
        await self.start_release.wait()
        if self.start_error is not None:
            raise self.start_error
        self.actions.append("service-started")

    def subscribe_updates(self):
        self.actions.append("subscribe")
        if self.subscribe_error is not None:
            raise self.subscribe_error
        return self.subscription

    async def get_session_history(self, *, after_sequence=0):
        self.actions.append(f"history:{after_sequence}")
        return self.history

    async def list_sessions(self):
        self.actions.append("session-list")
        return self.session_summaries

    async def get_session_status(self):
        self.actions.append("session-status")
        return self.session_status

    async def discard_empty_session(self, session_id):
        self.actions.append(f"discard:{session_id}")
        self.discarded_sessions.append(session_id)
        return DiscardEmptySessionResultModel(
            workspace_key=self.workspace_key,
            session_id=session_id,
            status=(
                "not_empty"
                if session_id == self.session_id and self.submissions
                else "discarded"
            ),
        )

    async def submit(
        self, prompt, *, submission_id, resources=(), display_text=None
    ):
        del submission_id
        task_id = f"task-{len(self.submissions) + 1}"
        self.actions.append(f"submit:{prompt}")
        self.submissions.append(prompt)
        self.submission_images.append(tuple(resources))
        self.display_text = display_text
        if self.submit_error is not None:
            raise self.submit_error
        self.subscription.queue.put_nowait(
            RuntimeUpdateModel(
                workspace_key="workspace",
                session_id=self.session_id,
                task_id=task_id,
                type="user.message",
                payload={
                    "text": prompt if display_text is None else display_text,
                    "resources": [],
                },
                occurred_at=datetime.now(UTC),
            )
        )
        await asyncio.sleep(0)
        if self.publish_queued_before_return:
            self.subscription.publish(
                "user-input",
                runtime_update(
                    "task.accepted",
                    task_id=task_id,
                    payload={"queue_position": 0},
                ),
            )
            await asyncio.sleep(0)
        return SubmitAccepted(task_id=task_id, queue_position=0)

    async def close(self) -> None:
        self.actions.append("service-stop")
        self.stopped = True
        self.subscription.close()
        if self.stop_error is not None:
            raise self.stop_error

    async def cancel_task(self, task_id, reason=None):
        self.cancelled_tasks.append((task_id, reason))
        if self.cancel_error is not None:
            raise self.cancel_error
        return TaskOperationResult(task_id=task_id, status="accepted")

    async def get_task_status(self, task_id):
        return self.task_statuses.get(
            task_id, {"task_id": task_id, "lifecycle": "running"}
        )


class ReconnectingService(ControlledService):
    async def reconnect(self):
        self.actions.append("reconnect")
        self.subscription = ControlledSubscription(self.actions)
        return self.subscription

def make_app(
    service: ControlledService,
    workspace_path,
    *,
    projector_registry: ProjectorRegistry | None = None,
) -> IcarusTextualApp:
    async def runtime_factory(session_id, create_if_missing):
        del session_id, create_if_missing
        service.actions.append("factory")
        return service

    return IcarusTextualApp(
        runtime_factory=runtime_factory,
        workspace_path=workspace_path,
        projector_registry=projector_registry,
    )


def make_session_app(
    current: ControlledService,
    candidates: dict[str | None, ControlledService],
    workspace_path,
):
    calls = []

    async def runtime_factory(session_id, create_if_missing):
        calls.append((session_id, create_if_missing))
        if len(calls) == 1:
            return current
        return candidates[session_id]

    return (
        IcarusTextualApp(
            runtime_factory=runtime_factory,
            workspace_path=workspace_path,
        ),
        calls,
    )


async def wait_until(pilot, predicate: Callable[[], bool]) -> None:
    async def wait() -> None:
        while not predicate():
            await pilot.pause()
            await asyncio.sleep(0)

    await asyncio.wait_for(wait(), timeout=2)


async def enter_text(pilot, text: str) -> None:
    keys = ["ctrl+j" if character == "\n" else character for character in text]
    await pilot.press(*keys, "enter")
    await pilot.pause()


def finish_event(task_id: str, status="completed") -> RuntimeUpdateModel:
    return runtime_update(
        "task.finished",
        task_id=task_id,
        payload={"status": status, "run_id": None},
    )


def runtime_update(
    update_type, *, task_id="task-1", payload=None
) -> RuntimeUpdateModel:
    return RuntimeUpdateModel(
        workspace_key="workspace",
        session_id="test-session",
        task_id=task_id,
        type=update_type,
        payload=payload or {},
        occurred_at=datetime.now(UTC),
    )


def _history_update(sequence, update_type, payload=None):
    return RuntimeUpdateModel(
        workspace_key="workspace",
        session_id="test-session",
        task_id="historical-task",
        type=update_type,
        payload=payload or {},
        occurred_at=datetime(2026, 1, 1, tzinfo=UTC),
        sequence=sequence,
    )


class FailingProjector:
    def project(self, event):
        del event
        raise RuntimeError("projector exploded")


class NotificationThenFinishProjector:
    def project(self, event):
        return (
            ShowNotification(level="information", text="finished"),
            FinishTurn(task_id=event.task_id, status="completed"),
        )


class UnknownActionProjector:
    def project(self, event):
        del event
        return (object(),)


def registry_with(source: str, projector) -> ProjectorRegistry:
    registry = ProjectorRegistry()
    registry.register(source, projector)
    return registry


def test_app启动订阅后ready并保持composer焦点(tmp_path):
    async def run():
        service = ControlledService()
        app = make_app(service, tmp_path)
        async with app.run_test() as pilot:
            await wait_until(
                pilot, lambda: app.chat_state.phase == RuntimePhase.READY
            )
            focused = app.focused
            status = str(app.query_one(RuntimeStatusBar).render())
            app.request_shutdown(return_code=0)
            await wait_until(pilot, lambda: service.stopped)
        return service, app, focused, status

    service, app, focused, status = asyncio.run(run())

    assert service.actions.index("service-started") < service.actions.index(
        "subscribe"
    )
    assert isinstance(focused, PersistentComposer)
    assert "Ready" not in status
    assert "Session" not in status
    assert service.stopped is True
    assert app.return_code == 0


def test_resume选择session后复用历史激活路径并清理旧空session(tmp_path):
    async def run():
        current = ControlledService()
        current.session_id = "current-empty"
        current.session_status["session_id"] = current.session_id
        current.session_summaries = (
            SessionSummaryModel(
                session_id="old-session",
                first_user_input="old question",
            ),
        )
        target = ControlledService()
        target.session_id = "old-session"
        target.session_status["session_id"] = target.session_id
        target.history = SessionHistoryModel(
            records=(
                RuntimeUpdateModel(
                    workspace_key="workspace",
                    session_id="old-session",
                    task_id="old-task",
                    type="user.message",
                    payload={"text": "old question", "resources": []},
                    occurred_at=datetime(2026, 1, 1, tzinfo=UTC),
                    sequence=1,
                ),
            ),
            history_cursor=1,
        )
        app, calls = make_session_app(
            current, {"old-session": target}, tmp_path
        )
        async with app.run_test() as pilot:
            await wait_until(
                pilot, lambda: app.chat_state.phase == RuntimePhase.READY
            )
            await enter_text(pilot, "/resume")
            await wait_until(pilot, lambda: isinstance(app.screen, SessionPicker))
            await pilot.press("enter")
            await wait_until(
                pilot,
                lambda: (
                    app.service is target
                    and app.chat_state.phase == RuntimePhase.READY
                ),
            )
            result = (
                calls,
                [item.message_text for item in app.query(UserMessage)],
                current.stopped,
                tuple(target.discarded_sessions),
                app._last_sequence,
                app._session_has_user_input,
            )
            app.request_shutdown(return_code=0)
            await wait_until(pilot, lambda: target.stopped)
            return result

    calls, messages, old_stopped, discarded, cursor, has_input = asyncio.run(
        run()
    )
    assert calls == [(None, True), ("old-session", False)]
    assert messages == ["old question"]
    assert old_stopped is True
    assert discarded == ("current-empty",)
    assert cursor == 1
    assert has_input is True


def test_clear从非空session创建新session并显示空会话(tmp_path):
    async def run():
        current = ControlledService()
        current.session_id = "current"
        current.session_status["session_id"] = current.session_id
        current.history = SessionHistoryModel(
            records=(
                RuntimeUpdateModel(
                    workspace_key="workspace",
                    session_id="current",
                    task_id="task",
                    type="user.message",
                    payload={"text": "keep me", "resources": []},
                    occurred_at=datetime(2026, 1, 1, tzinfo=UTC),
                    sequence=1,
                ),
            ),
            history_cursor=1,
        )
        fresh = ControlledService()
        fresh.session_id = "fresh"
        fresh.session_status["session_id"] = fresh.session_id
        app, calls = make_session_app(current, {None: fresh}, tmp_path)
        async with app.run_test() as pilot:
            await wait_until(
                pilot, lambda: app.chat_state.phase == RuntimePhase.READY
            )
            await enter_text(pilot, "/clear")
            await wait_until(
                pilot,
                lambda: (
                    app.service is fresh
                    and app.chat_state.phase == RuntimePhase.READY
                ),
            )
            result = (
                calls,
                len(app.query(UserMessage)),
                len(app.query(".welcome-message")),
                current.stopped,
                tuple(fresh.discarded_sessions),
            )
            app.request_shutdown(return_code=0)
            await wait_until(pilot, lambda: fresh.stopped)
            return result

    calls, user_count, welcome_count, old_stopped, discarded = asyncio.run(
        run()
    )
    assert calls == [(None, True), (None, True)]
    assert user_count == 0
    assert welcome_count == 1
    assert old_stopped is True
    assert "current" not in discarded


def test_clear在空session不创建另一个session(tmp_path):
    async def run():
        current = ControlledService()
        app, calls = make_session_app(current, {}, tmp_path)
        async with app.run_test() as pilot:
            await wait_until(
                pilot, lambda: app.chat_state.phase == RuntimePhase.READY
            )
            await enter_text(pilot, "/clear")
            await wait_until(
                pilot,
                lambda: (
                    app.chat_state.phase == RuntimePhase.READY
                    and app._session_operation_worker is not None
                    and app._session_operation_worker.is_finished
                ),
            )
            result = calls, app.service is current, current.submissions
            app.request_shutdown(return_code=0)
            await wait_until(pilot, lambda: current.stopped)
            return result

    calls, unchanged, submissions = asyncio.run(run())
    assert calls == [(None, True)]
    assert unchanged is True
    assert submissions == []


def test_clear候选启动失败时保留当前session并清理候选(tmp_path):
    async def run():
        current = ControlledService()
        current.session_id = "current"
        current.session_status["session_id"] = current.session_id
        current.history = SessionHistoryModel(
            records=(
                RuntimeUpdateModel(
                    workspace_key="workspace",
                    session_id="current",
                    task_id="task",
                    type="user.message",
                    payload={"text": "keep me", "resources": []},
                    occurred_at=datetime(2026, 1, 1, tzinfo=UTC),
                    sequence=1,
                ),
            ),
            history_cursor=1,
        )
        failed = ControlledService(start_error=RuntimeError("start failed"))
        failed.session_id = "failed-candidate"
        app, calls = make_session_app(current, {None: failed}, tmp_path)
        async with app.run_test() as pilot:
            await wait_until(
                pilot, lambda: app.chat_state.phase == RuntimePhase.READY
            )
            await enter_text(pilot, "/clear")
            await wait_until(
                pilot,
                lambda: (
                    app._session_operation_worker is not None
                    and app._session_operation_worker.is_finished
                ),
            )
            result = (
                calls,
                app.service is current,
                [item.message_text for item in app.query(UserMessage)],
                app.chat_state.phase,
                current.stopped,
                failed.stopped,
                tuple(current.discarded_sessions),
            )
            app.request_shutdown(return_code=0)
            await wait_until(pilot, lambda: current.stopped)
            return result

    (
        calls,
        unchanged,
        messages,
        phase,
        current_stopped,
        candidate_stopped,
        discarded,
    ) = asyncio.run(run())
    assert calls == [(None, True), (None, True)]
    assert unchanged is True
    assert messages == ["keep me"]
    assert phase == RuntimePhase.READY
    assert current_stopped is False
    assert candidate_stopped is True
    assert discarded == ("failed-candidate",)


def test_session命令在运行中拒绝且不发送给agent(tmp_path):
    async def run():
        service = ControlledService()
        app = make_app(service, tmp_path)
        async with app.run_test() as pilot:
            await wait_until(
                pilot, lambda: app.chat_state.phase == RuntimePhase.READY
            )
            await enter_text(pilot, "first")
            await wait_until(
                pilot, lambda: app.chat_state.phase == RuntimePhase.RUNNING
            )
            await enter_text(pilot, "/clear")
            await pilot.pause()
            result = service.submissions[:], app.chat_state.pending_items
            app.request_shutdown(return_code=0)
            await wait_until(pilot, lambda: service.stopped)
            return result

    submissions, pending = asyncio.run(run())
    assert submissions == ["first"]
    assert pending == ()


def test_session命令带图片时恢复完整草稿(tmp_path):
    async def run():
        service = ControlledService()
        app = make_app(service, tmp_path)
        image_path = tmp_path / "image.png"
        image_path.write_bytes(b"image")
        async with app.run_test() as pilot:
            await wait_until(
                pilot, lambda: app.chat_state.phase == RuntimePhase.READY
            )
            composer = app.query_one(PersistentComposer)
            composer.load_text("/resume")
            image = composer.attach_image(image_path)
            await pilot.press("enter")
            await pilot.pause()
            result = composer.text, composer.images, service.submissions
            app.request_shutdown(return_code=0)
            await wait_until(pilot, lambda: service.stopped)
            return result, image

    (text, images, submissions), image = asyncio.run(run())
    assert text.replace(image.marker, "") == "/resume"
    assert images == (image,)
    assert submissions == []


@pytest.mark.parametrize(
    ("target_status", "start_error"),
    [
        ({"lifecycle": "running"}, None),
        ({"lifecycle": "ready"}, RuntimeError("missing Session")),
    ],
)
def test_resume目标失败时保留当前session和conversation(
    tmp_path, target_status, start_error
):
    async def run():
        current = ControlledService()
        current.session_id = "current"
        current.session_status["session_id"] = current.session_id
        current.session_summaries = (
            SessionSummaryModel(
                session_id="target", first_user_input="target question"
            ),
        )
        current.history = SessionHistoryModel(
            records=(
                RuntimeUpdateModel(
                    workspace_key="workspace",
                    session_id="current",
                    task_id="current-task",
                    type="user.message",
                    payload={"text": "current question", "resources": []},
                    occurred_at=datetime(2026, 1, 1, tzinfo=UTC),
                    sequence=1,
                ),
            ),
            history_cursor=1,
        )
        target = ControlledService(start_error=start_error)
        target.session_id = "target"
        target.session_status.update(target_status)
        target.session_status["session_id"] = target.session_id
        app, calls = make_session_app(current, {"target": target}, tmp_path)
        async with app.run_test() as pilot:
            await wait_until(
                pilot, lambda: app.chat_state.phase == RuntimePhase.READY
            )
            await enter_text(pilot, "/resume")
            await wait_until(pilot, lambda: isinstance(app.screen, SessionPicker))
            await pilot.press("enter")
            await wait_until(
                pilot,
                lambda: (
                    app._session_operation_worker is not None
                    and app._session_operation_worker.is_finished
                ),
            )
            result = (
                calls,
                app.service is current,
                [item.message_text for item in app.query(UserMessage)],
                app.chat_state.phase,
                current.stopped,
                target.stopped,
            )
            app.request_shutdown(return_code=0)
            await wait_until(pilot, lambda: current.stopped)
            return result

    calls, unchanged, messages, phase, current_stopped, target_stopped = (
        asyncio.run(run())
    )
    assert calls == [(None, True), ("target", False)]
    assert unchanged is True
    assert messages == ["current question"]
    assert phase == RuntimePhase.READY
    assert current_stopped is False
    assert target_stopped is True


def test_切换后忽略旧subscription迟到的失败消息(tmp_path):
    async def run():
        current = ControlledService()
        current.session_id = "current-empty"
        current.session_status["session_id"] = current.session_id
        current.session_summaries = (
            SessionSummaryModel(
                session_id="target", first_user_input="target question"
            ),
        )
        old_subscription = current.subscription
        target = ReconnectingService()
        target.session_id = "target"
        target.session_status["session_id"] = target.session_id
        target.history = SessionHistoryModel(
            records=(
                RuntimeUpdateModel(
                    workspace_key="workspace",
                    session_id="target",
                    task_id="target-task",
                    type="user.message",
                    payload={"text": "target question", "resources": []},
                    occurred_at=datetime(2026, 1, 1, tzinfo=UTC),
                    sequence=1,
                ),
            ),
            history_cursor=1,
        )
        app, _ = make_session_app(current, {"target": target}, tmp_path)
        async with app.run_test() as pilot:
            await wait_until(
                pilot, lambda: app.chat_state.phase == RuntimePhase.READY
            )
            await enter_text(pilot, "/resume")
            await wait_until(pilot, lambda: isinstance(app.screen, SessionPicker))
            await pilot.press("enter")
            await wait_until(pilot, lambda: app.service is target)

            app.post_message(
                RuntimeSubscriptionFailed(
                    ConnectionError("old stream closed"), old_subscription
                )
            )
            await pilot.pause()
            result = (
                app.service is target,
                "reconnect" in target.actions,
                app.chat_state.phase,
            )
            app.request_shutdown(return_code=0)
            await wait_until(pilot, lambda: target.stopped)
            return result

    unchanged, reconnected, phase = asyncio.run(run())
    assert unchanged is True
    assert reconnected is False
    assert phase == RuntimePhase.READY


def test_app恢复session退出时的消息工具错误和中断状态(tmp_path):
    async def run():
        service = ControlledService()
        service.history = SessionHistoryModel(
            history_cursor=6,
            records=(
                _history_update(1, "user.message", {"text": "hello", "resources": []}),
                _history_update(2, "task.started"),
                _history_update(3, "assistant.text_delta", {"step": 1, "text": "partial"}),
                _history_update(4, "tool.started", {"step": 1, "call_id": "call", "tool_name": "read", "arguments": {"path": "a"}}),
                _history_update(5, "task.error", {"fatal": True, "code": "stopped", "error_type": "RuntimeError", "message": "stopped", "step": 1, "run_id": "run"}),
                _history_update(6, "task.finished", {"status": "interrupted", "run_id": None, "recovered": True}),
            ),
        )
        app = make_app(service, tmp_path)
        async with app.run_test() as pilot:
            await wait_until(pilot, lambda: app.chat_state.phase == RuntimePhase.READY)
            result = (
                len(app.query(UserMessage)),
                app.query_one(AssistantMessage).markdown_text,
                str(app.query_one(ToolMessage).query_one(".tool-state").render()),
                len(app.query(ErrorMessage)),
                str(app.query_one(TurnStatusMessage).render()),
                app._last_sequence,
            )
            app.request_shutdown(return_code=0)
            await wait_until(pilot, lambda: service.stopped)
            return result

    assert asyncio.run(run()) == (
        1,
        "partial",
        "interrupted",
        1,
        "Task interrupted",
        6,
    )


def test_app历史sequence缺口不进入ready(tmp_path):
    async def run():
        service = ControlledService()
        service.history = SessionHistoryModel(
            records=(
                _history_update(2, "user.message", {"text": "gap"}),
            ),
            history_cursor=2,
        )
        app = make_app(service, tmp_path)
        async with app.run_test() as pilot:
            await wait_until(pilot, lambda: app.chat_state.phase == RuntimePhase.FAILED)
            result = str(app.query_one(RuntimeStatusBar).render())
            app.request_shutdown(return_code=0)
            await wait_until(pilot, lambda: service.stopped)
            return result

    assert "sequence gap" in asyncio.run(run())


def test_app历史在隐藏conversation和batch中完整构建后一次显示(
    monkeypatch, tmp_path
):
    observed = []
    original = ConversationView.apply_action

    async def observe(self, action):
        observed.append((self.display, self.app._batch_count))
        return await original(self, action)

    monkeypatch.setattr(ConversationView, "apply_action", observe)

    async def run():
        service = ControlledService()
        service.history = SessionHistoryModel(
            records=(
                _history_update(
                    1,
                    "user.message",
                    {"text": "restored", "resources": []},
                ),
                _history_update(
                    2,
                    "assistant.text_delta",
                    {"step": 1, "text": "complete"},
                ),
                _history_update(
                    3,
                    "task.finished",
                    {"status": "completed", "run_id": "run"},
                ),
            ),
            history_cursor=3,
        )
        app = make_app(service, tmp_path)
        async with app.run_test() as pilot:
            await wait_until(
                pilot, lambda: app.chat_state.phase == RuntimePhase.READY
            )
            conversation = app.query_one(ConversationView)
            result = (
                tuple(observed),
                conversation.display,
                app.query_one(AssistantMessage).markdown_text,
            )
            app.request_shutdown(return_code=0)
            await wait_until(pilot, lambda: service.stopped)
            return result

    states, visible, assistant = asyncio.run(run())
    assert states
    assert all(display is False and batch_count > 0 for display, batch_count in states)
    assert visible is True
    assert assistant == "complete"


def test_starting期间可排队且ready后自动提交(tmp_path):
    async def run():
        service = ControlledService(block_start=True)
        app = make_app(service, tmp_path)
        async with app.run_test() as pilot:
            await service.start_entered.wait()
            initial_status = str(app.query_one(RuntimeStatusBar).render())
            await enter_text(pilot, "queued while starting")
            assert app.chat_state.pending_items == ("queued while starting",)
            assert service.submissions == []
            waiting_status = str(app.query_one(RuntimeStatusBar).render())

            service.start_release.set()
            await wait_until(pilot, lambda: bool(service.submissions))
            state = (
                tuple(service.submissions),
                app.chat_state.active_task_id,
                app.chat_state.pending_items,
            )
            app.request_shutdown(return_code=0)
            await wait_until(pilot, lambda: service.stopped)
            return initial_status, waiting_status, state

    initial_status, waiting_status, state = asyncio.run(run())
    submissions, active_task_id, pending = state

    assert "Starting" not in initial_status
    assert "Initializing" not in initial_status
    assert "Initializing" in waiting_status
    assert submissions == ("queued while starting",)
    assert active_task_id == "task-1"
    assert pending == ()


def test_ctrl_v图片生成marker并向runtime提交映射和路径(
    monkeypatch, tmp_path
):
    clipboard_image = ClipboardImage(b"png-data", "image/png", "png")
    monkeypatch.setattr(
        "apps.tui.src.app.read_clipboard_image",
        lambda: clipboard_image,
    )

    async def run():
        service = ControlledService()
        app = make_app(service, tmp_path)
        async with app.run_test() as pilot:
            await wait_until(
                pilot, lambda: app.chat_state.phase == RuntimePhase.READY
            )
            await pilot.press(*"请查看 ", "ctrl+v")
            composer = app.query_one(PersistentComposer)
            await wait_until(pilot, lambda: "[#image1]" in composer.text)
            draft = composer.text
            image_path = composer.images[0].path
            mode = image_path.stat().st_mode & 0o777
            data = image_path.read_bytes()

            await pilot.press("enter")
            await wait_until(pilot, lambda: bool(service.submissions))
            user_message = app.query_one(".user-message .message-content", Static)
            result = (
                draft,
                image_path,
                mode,
                data,
                service.submissions[0],
                service.submission_images[0],
                str(user_message.render()),
            )
            app.request_shutdown(return_code=0)
            await wait_until(pilot, lambda: service.stopped)
            return result

    (
        draft,
        image_path,
        mode,
        data,
        prompt,
        image_paths,
        user_text,
    ) = asyncio.run(run())

    assert draft == "请查看 [#image1]"
    assert mode == 0o600
    assert data == b"png-data"
    assert prompt == (
        "请查看 [#image1]\n\n"
        "<attached_images>\n"
        "[#image1] 对应第 1 张附件图片\n"
        "</attached_images>"
    )
    assert len(image_paths) == 1
    assert image_paths[0].resource_id == image_path.name
    assert user_text == "请查看 [#image1]"
    assert image_path.exists() is False


def test_ctrl_v没有图片时回退textual文本剪贴板(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "apps.tui.src.app.read_clipboard_image", lambda: None
    )

    async def run():
        service = ControlledService()
        app = make_app(service, tmp_path)
        async with app.run_test() as pilot:
            await wait_until(
                pilot, lambda: app.chat_state.phase == RuntimePhase.READY
            )
            app.copy_to_clipboard("plain text")
            await pilot.press("ctrl+v")
            composer = app.query_one(PersistentComposer)
            await wait_until(pilot, lambda: composer.text == "plain text")
            result = (composer.text, composer.images)
            app.request_shutdown(return_code=0)
            await wait_until(pilot, lambda: service.stopped)
            return result

    assert asyncio.run(run()) == ("plain text", ())


def test_ctrl_v在后台线程读取系统剪贴板(monkeypatch, tmp_path):
    caller_threads = []

    def read_image():
        caller_threads.append(threading.get_ident())
        return None

    monkeypatch.setattr(
        "apps.tui.src.app.read_clipboard_image", read_image
    )

    async def run():
        service = ControlledService()
        app = make_app(service, tmp_path)
        event_loop_thread = threading.get_ident()
        async with app.run_test() as pilot:
            await wait_until(
                pilot, lambda: app.chat_state.phase == RuntimePhase.READY
            )
            await pilot.press("ctrl+v")
            await wait_until(pilot, lambda: bool(caller_threads))
            app.request_shutdown(return_code=0)
            await wait_until(pilot, lambda: service.stopped)
        return event_loop_thread

    event_loop_thread = asyncio.run(run())

    assert caller_threads[0] != event_loop_thread


def test_ctrl_v读取失败只显示非致命通知(monkeypatch, tmp_path):
    def fail_read():
        raise ClipboardImageReadError("clipboard unavailable")

    monkeypatch.setattr(
        "apps.tui.src.app.read_clipboard_image", fail_read
    )

    async def run():
        service = ControlledService()
        app = make_app(service, tmp_path)
        notifications = []
        async with app.run_test() as pilot:
            await wait_until(
                pilot, lambda: app.chat_state.phase == RuntimePhase.READY
            )
            monkeypatch.setattr(
                app,
                "notify",
                lambda message, **kwargs: notifications.append(
                    (message, kwargs)
                ),
            )
            await pilot.press("ctrl+v")
            await wait_until(pilot, lambda: bool(notifications))
            result = (
                app.chat_state.phase,
                app.query_one(PersistentComposer).text,
                notifications,
            )
            app.request_shutdown(return_code=0)
            await wait_until(pilot, lambda: service.stopped)
            return result

    phase, text, notifications = asyncio.run(run())

    assert phase == RuntimePhase.READY
    assert text == ""
    assert "clipboard unavailable" in notifications[0][0]
    assert notifications[0][1]["severity"] == "warning"


def test图片提交失败后ctrl_c恢复文字和附件(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "apps.tui.src.app.read_clipboard_image",
        lambda: ClipboardImage(b"png-data", "image/png", "png"),
    )

    async def run():
        service = ControlledService(submit_error=RuntimeError("broken"))
        app = make_app(service, tmp_path)
        async with app.run_test() as pilot:
            await wait_until(
                pilot, lambda: app.chat_state.phase == RuntimePhase.READY
            )
            await pilot.press("ctrl+v")
            composer = app.query_one(PersistentComposer)
            await wait_until(pilot, lambda: bool(composer.images))
            image_path = composer.images[0].path
            await pilot.press("enter")
            await wait_until(
                pilot, lambda: app.chat_state.phase == RuntimePhase.FAILED
            )
            pending = app.chat_state.pending_messages[0]
            await pilot.press("ctrl+c")
            await pilot.pause()
            result = (
                pending,
                composer.text,
                composer.images,
                app.chat_state.pending_messages,
                image_path.exists(),
            )
            app.request_shutdown(return_code=0)
            await wait_until(pilot, lambda: service.stopped)
            return result, image_path

    result, image_path = asyncio.run(run())
    pending, text, images, queued, existed_before_shutdown = result

    assert text == "[#image1]"
    assert images == pending.images
    assert queued == ()
    assert existed_before_shutdown is True
    assert image_path.exists() is False


def test_factory失败前保持静默且提交后保留队首并显示错误(tmp_path):
    async def run():
        async def failing_factory(session_id, create_if_missing):
            del session_id, create_if_missing
            raise RuntimeError("factory broken")

        app = IcarusTextualApp(
            runtime_factory=failing_factory,
            workspace_path=tmp_path,
        )
        async with app.run_test() as pilot:
            await wait_until(
                pilot, lambda: app.chat_state.phase == RuntimePhase.FAILED
            )
            initial_status = str(app.query_one(RuntimeStatusBar).render())
            await enter_text(pilot, "keep after factory failure")
            failed_status = str(app.query_one(RuntimeStatusBar).render())
            pending = app.chat_state.pending_items
            app.request_shutdown(return_code=0)
            await wait_until(pilot, lambda: app.return_code == 0)
            return initial_status, failed_status, pending, app.service

    initial_status, failed_status, pending, service = asyncio.run(run())

    assert "Failed" not in initial_status
    assert "factory broken" not in initial_status
    assert "Failed" in failed_status
    assert "factory broken" in failed_status
    assert pending == ("keep after factory failure",)
    assert service is None


def test_start失败保留队首且退出只停止service一次(tmp_path):
    async def run():
        service = ControlledService(
            block_start=True,
            start_error=RuntimeError("start broken"),
        )
        app = make_app(service, tmp_path)
        async with app.run_test() as pilot:
            await service.start_entered.wait()
            await enter_text(pilot, "keep after start failure")
            service.start_release.set()
            await wait_until(
                pilot, lambda: app.chat_state.phase == RuntimePhase.FAILED
            )
            status = str(app.query_one(RuntimeStatusBar).render())
            pending = app.chat_state.pending_items
            app.request_shutdown(return_code=0)
            await wait_until(pilot, lambda: service.stopped)
            return status, pending, tuple(service.actions)

    status, pending, actions = asyncio.run(run())

    assert "start broken" in status
    assert pending == ("keep after start failure",)
    assert actions.count("service-stop") == 1


def test_subscribe失败立即停止service并在退出时不重复stop(tmp_path):
    async def run():
        service = ControlledService(
            subscribe_error=RuntimeError("subscribe broken")
        )
        app = make_app(service, tmp_path)
        async with app.run_test() as pilot:
            await wait_until(
                pilot, lambda: app.chat_state.phase == RuntimePhase.FAILED
            )
            await enter_text(pilot, "keep after subscribe failure")
            status = str(app.query_one(RuntimeStatusBar).render())
            pending = app.chat_state.pending_items
            app.request_shutdown(return_code=0)
            await wait_until(pilot, lambda: app.return_code == 0)
            return status, pending, tuple(service.actions), app.service

    status, pending, actions, app_service = asyncio.run(run())

    assert "subscribe broken" in status
    assert pending == ("keep after subscribe failure",)
    assert actions.count("service-stop") == 1
    assert app_service is None


def test_subscription运行中失败后忽略迟到终态且不调度队首(tmp_path):
    async def run():
        service = ControlledService()
        app = make_app(service, tmp_path)
        async with app.run_test() as pilot:
            await wait_until(
                pilot, lambda: app.chat_state.phase == RuntimePhase.READY
            )
            await enter_text(pilot, "active")
            await enter_text(pilot, "must stay queued")
            app.post_message(
                RuntimeSubscriptionFailed(RuntimeError("stream exploded"))
            )
            await wait_until(
                pilot, lambda: app.chat_state.phase == RuntimePhase.FAILED
            )
            service.subscription.publish(
                "user-input", finish_event("task-1")
            )
            await pilot.pause()
            result = (
                app.chat_state.phase,
                app.chat_state.active_task_id,
                app.chat_state.pending_items,
                tuple(service.submissions),
            )
            app.request_shutdown(return_code=0)
            await wait_until(pilot, lambda: service.stopped)
            return result

    phase, active, pending, submissions = asyncio.run(run())

    assert phase == RuntimePhase.FAILED
    assert active == "task-1"
    assert pending == ("must stay queued",)
    assert submissions == ("active",)


def test_subscription断线后重连并保留运行中task(tmp_path):
    async def run():
        service = ReconnectingService()
        app = make_app(service, tmp_path)
        async with app.run_test() as pilot:
            await wait_until(
                pilot, lambda: app.chat_state.phase == RuntimePhase.READY
            )
            await enter_text(pilot, "active")
            app.post_message(
                RuntimeSubscriptionFailed(ConnectionError("disconnected"))
            )
            await wait_until(pilot, lambda: "reconnect" in service.actions)
            result = (
                app.chat_state.phase,
                app.chat_state.active_task_id,
                app._fatal_failure,
                str(app.query_one(RuntimeStatusBar).render()),
            )
            app.request_shutdown(return_code=0)
            await wait_until(pilot, lambda: service.stopped)
            return result

    phase, active, fatal, status = asyncio.run(run())
    assert phase == RuntimePhase.RUNNING
    assert active == "task-1"
    assert fatal is False
    assert "Reconnected" in status


def test运行中输入按FIFO排队并在finish后每次只提交一条(tmp_path):
    async def run():
        service = ControlledService()
        app = make_app(service, tmp_path)
        async with app.run_test() as pilot:
            await wait_until(
                pilot, lambda: app.chat_state.phase == RuntimePhase.READY
            )
            await enter_text(pilot, "first")
            await enter_text(pilot, "second")
            await enter_text(pilot, "third")
            assert service.submissions == ["first"]
            assert app.chat_state.pending_items == ("second", "third")
            assert app.query_one(QueuePanel).items == ("second", "third")

            service.subscription.publish("user-input", finish_event("task-1"))
            await wait_until(pilot, lambda: len(service.submissions) == 2)
            after_first = (
                tuple(service.submissions),
                app.chat_state.pending_items,
                app.chat_state.active_task_id,
            )

            service.subscription.publish("user-input", finish_event("task-2"))
            await wait_until(pilot, lambda: len(service.submissions) == 3)
            after_second = (
                tuple(service.submissions),
                app.chat_state.pending_items,
                app.chat_state.active_task_id,
            )
            app.request_shutdown(return_code=0)
            await wait_until(pilot, lambda: service.stopped)
            return after_first, after_second

    after_first, after_second = asyncio.run(run())

    assert after_first == (("first", "second"), ("third",), "task-2")
    assert after_second == (
        ("first", "second", "third"),
        (),
        "task-3",
    )


def test_submit返回前到达queued_event不会被误丢弃(tmp_path):
    async def run():
        service = ControlledService(publish_queued_before_return=True)
        app = make_app(service, tmp_path)
        async with app.run_test() as pilot:
            await wait_until(
                pilot, lambda: app.chat_state.phase == RuntimePhase.READY
            )
            await enter_text(pilot, "race")
            await wait_until(
                pilot,
                lambda: "Accepted by runtime"
                in str(app.query_one(RuntimeStatusBar).render()),
            )
            result = (
                app.chat_state.active_task_id,
                app.projector_registry.unrelated_update_count,
                str(app.query_one(RuntimeStatusBar).render()),
            )
            app.request_shutdown(return_code=0)
            await wait_until(pilot, lambda: service.stopped)
            return result

    active, unrelated_count, status = asyncio.run(run())

    assert active == "task-1"
    assert unrelated_count == 0
    assert "Accepted by runtime" in status


def test正常完成后状态栏只显示一次ready(tmp_path):
    async def run():
        service = ControlledService()
        app = make_app(service, tmp_path)
        async with app.run_test() as pilot:
            await wait_until(
                pilot, lambda: app.chat_state.phase == RuntimePhase.READY
            )
            await enter_text(pilot, "first")
            service.subscription.publish(
                "user-input", finish_event("task-1")
            )
            await wait_until(
                pilot, lambda: app.chat_state.active_task_id is None
            )
            status = str(app.query_one(RuntimeStatusBar).render())
            app.request_shutdown(return_code=0)
            await wait_until(pilot, lambda: service.stopped)
            return status

    status = asyncio.run(run())

    assert status.startswith("Ready · Enter submit")
    assert "Ready · Ready" not in status


def test_agent输出期间草稿光标和焦点保持不变(tmp_path):
    async def run():
        service = ControlledService()
        app = make_app(service, tmp_path)
        async with app.run_test() as pilot:
            await wait_until(
                pilot, lambda: app.chat_state.phase == RuntimePhase.READY
            )
            await enter_text(pilot, "first")
            await pilot.press(*"draft", "left", "X")
            composer = app.query_one(PersistentComposer)
            before = (composer.text, composer.cursor_location, app.focused)

            service.subscription.publish(
                "agent",
                runtime_update(
                    "assistant.text_delta",
                    task_id="task-1",
                    payload={"step": 1, "text": "**streaming**"},
                ),
            )
            await wait_until(
                pilot, lambda: len(app.query(AssistantMessage)) == 1
            )
            after = (composer.text, composer.cursor_location, app.focused)
            markdown = app.query_one(AssistantMessage).markdown_text
            app.request_shutdown(return_code=0)
            await wait_until(pilot, lambda: service.stopped)
            return before, after, markdown

    before, after, markdown = asyncio.run(run())

    assert before == after
    assert before[0] == "drafXt"
    assert isinstance(after[2], PersistentComposer)
    assert markdown == "**streaming**"


def test_ctrl_c依次清草稿撤回队尾取消任务并在空闲退出(tmp_path):
    async def run():
        service = ControlledService()
        app = make_app(service, tmp_path)
        async with app.run_test() as pilot:
            await wait_until(
                pilot, lambda: app.chat_state.phase == RuntimePhase.READY
            )
            await enter_text(pilot, "first")
            await enter_text(pilot, "second\nline")

            await pilot.press(*"draft", "ctrl+c")
            await pilot.pause()
            composer = app.query_one(PersistentComposer)
            after_clear = (composer.text, app.chat_state.pending_items)

            await pilot.press("ctrl+c")
            await pilot.pause()
            after_restore = (
                composer.text,
                composer.cursor_location,
                app.chat_state.pending_items,
            )

            await pilot.press("ctrl+c")
            await pilot.pause()
            after_second_clear = composer.text

            await pilot.press("ctrl+c")
            await wait_until(pilot, lambda: bool(service.cancelled_tasks))
            cancelling_status = str(app.query_one(RuntimeStatusBar).render())
            still_active = app.chat_state.active_task_id

            service.subscription.publish(
                "user-input", finish_event("task-1", "cancelled")
            )
            await wait_until(
                pilot, lambda: app.chat_state.active_task_id is None
            )
            await pilot.press("ctrl+c")
            await wait_until(pilot, lambda: service.stopped)
            return (
                after_clear,
                after_restore,
                after_second_clear,
                cancelling_status,
                still_active,
                tuple(service.cancelled_tasks),
                app.return_code,
            )

    (
        after_clear,
        after_restore,
        after_second_clear,
        cancelling_status,
        still_active,
        cancelled_tasks,
        return_code,
    ) = asyncio.run(run())

    assert after_clear == ("", ("second\nline",))
    assert after_restore == ("second\nline", (1, 4), ())
    assert after_second_clear == ""
    assert "Cancelling" in cancelling_status
    assert still_active == "task-1"
    assert cancelled_tasks == (("task-1", "user_requested"),)
    assert return_code == 0


def test_ctrl_c取消失败时保留active_task并显示错误(tmp_path):
    async def run():
        service = ControlledService(cancel_error=RuntimeError("cancel broken"))
        app = make_app(service, tmp_path)
        async with app.run_test() as pilot:
            await wait_until(
                pilot, lambda: app.chat_state.phase == RuntimePhase.READY
            )
            await enter_text(pilot, "active")
            await wait_until(
                pilot, lambda: app.chat_state.active_task_id == "task-1"
            )

            await pilot.press("ctrl+c")
            await wait_until(pilot, lambda: bool(service.cancelled_tasks))
            await wait_until(
                pilot,
                lambda: "Cancellation failed"
                in str(app.query_one(RuntimeStatusBar).render()),
            )
            result = (
                app.chat_state.active_task_id,
                app.chat_state.phase,
                str(app.query_one(RuntimeStatusBar).render()),
            )
            app.request_shutdown(return_code=0)
            await wait_until(pilot, lambda: service.stopped)
            return result

    active_task_id, phase, status = asyncio.run(run())

    assert active_task_id == "task-1"
    assert phase == RuntimePhase.RUNNING
    assert "Cancellation failed" in status


def test_submit失败保留队首且可由ctrl_c恢复(tmp_path):
    async def run():
        service = ControlledService(submit_error=RuntimeError("broken"))
        app = make_app(service, tmp_path)
        async with app.run_test() as pilot:
            await wait_until(
                pilot, lambda: app.chat_state.phase == RuntimePhase.READY
            )
            await enter_text(pilot, "  keep\n    indentation  ")
            await wait_until(
                pilot, lambda: app.chat_state.phase == RuntimePhase.FAILED
            )
            pending_before = app.chat_state.pending_items
            await pilot.press("ctrl+c")
            await pilot.pause()
            composer = app.query_one(PersistentComposer)
            restored = composer.text
            pending_after = app.chat_state.pending_items
            app.request_shutdown(return_code=0)
            await wait_until(pilot, lambda: service.stopped)
            return pending_before, restored, pending_after

    pending_before, restored, pending_after = asyncio.run(run())

    assert pending_before == ("  keep\n    indentation  ",)
    assert restored == "  keep\n    indentation  "
    assert pending_after == ()


def test_projector失败进入fatal并保留当前任务队列和草稿(tmp_path):
    async def run():
        service = ControlledService()
        app = make_app(
            service,
            tmp_path,
            projector_registry=registry_with("agent", FailingProjector()),
        )
        async with app.run_test() as pilot:
            await wait_until(
                pilot, lambda: app.chat_state.phase == RuntimePhase.READY
            )
            await enter_text(pilot, "active")
            await enter_text(pilot, "keep queued")
            composer = app.query_one(PersistentComposer)
            composer.load_text("keep draft")
            service.subscription.publish(
                "agent",
                runtime_update("agent"),
            )
            await wait_until(
                pilot, lambda: app.chat_state.phase == RuntimePhase.FAILED
            )
            result = (
                app.chat_state.active_task_id,
                app.chat_state.pending_items,
                composer.text,
                str(app.query_one(RuntimeStatusBar).render()),
                app._exception,
            )
            app.request_shutdown(return_code=0)
            await wait_until(pilot, lambda: service.stopped)
            return result

    active, pending, draft, status, exception = asyncio.run(run())

    assert active == "task-1"
    assert pending == ("keep queued",)
    assert draft == "keep draft"
    assert "event projection" in status
    assert "projector exploded" in status
    assert exception is None


def test_conversation更新失败后忽略后续event且不调度队首(
    monkeypatch, tmp_path
):
    async def fail_action(self, action):
        del self, action
        raise RuntimeError("conversation exploded")

    monkeypatch.setattr(ConversationView, "apply_action", fail_action)

    async def run():
        service = ControlledService()
        app = make_app(service, tmp_path)
        async with app.run_test() as pilot:
            await wait_until(
                pilot, lambda: app.chat_state.phase == RuntimePhase.READY
            )
            await enter_text(pilot, "active")
            await enter_text(pilot, "must not dispatch")
            service.subscription.publish(
                "agent",
                runtime_update(
                    "assistant.text_delta",
                    task_id="task-1",
                    payload={"step": 1, "text": "broken update"},
                ),
            )
            await wait_until(
                pilot, lambda: app.chat_state.phase == RuntimePhase.FAILED
            )
            service.subscription.publish(
                "user-input", finish_event("task-1")
            )
            await pilot.pause()
            await asyncio.sleep(0)
            result = (
                tuple(service.submissions),
                app.chat_state.active_task_id,
                app.chat_state.pending_items,
                str(app.query_one(RuntimeStatusBar).render()),
                app._exception,
            )
            app.request_shutdown(return_code=0)
            await wait_until(pilot, lambda: service.stopped)
            return result

    submissions, active, pending, status, exception = asyncio.run(run())

    assert submissions == ("active",)
    assert active == "task-1"
    assert pending == ("must not dispatch",)
    assert "ConversationView action" in status
    assert "conversation exploded" in status
    assert exception is None


def test_runtime接受后用户消息渲染失败不把消息重新入队(
    monkeypatch, tmp_path
):
    async def fail_user_message(self, text):
        del self, text
        raise RuntimeError("user message exploded")

    monkeypatch.setattr(
        ConversationView,
        "append_user_message",
        fail_user_message,
    )

    async def run():
        service = ControlledService(block_start=True)
        app = make_app(service, tmp_path)
        async with app.run_test() as pilot:
            await service.start_entered.wait()
            await enter_text(pilot, "accepted")
            await enter_text(pilot, "still pending")
            service.start_release.set()
            await wait_until(
                pilot, lambda: app.chat_state.phase == RuntimePhase.FAILED
            )
            result = (
                tuple(service.submissions),
                app.chat_state.active_task_id,
                app.chat_state.pending_items,
                str(app.query_one(RuntimeStatusBar).render()),
                app._exception,
            )
            app.request_shutdown(return_code=0)
            await wait_until(pilot, lambda: service.stopped)
            return result

    submissions, active, pending, status, exception = asyncio.run(run())

    assert submissions == ("accepted",)
    assert active == "task-1"
    assert pending == ("still pending",)
    assert "ConversationView action" in status
    assert "user message exploded" in status
    assert exception is None


def test_notification展示失败不阻止同一event完成任务(monkeypatch, tmp_path):
    async def run():
        service = ControlledService()
        app = make_app(
            service,
            tmp_path,
            projector_registry=registry_with(
                "test-source", NotificationThenFinishProjector()
            ),
        )
        async with app.run_test() as pilot:
            await wait_until(
                pilot, lambda: app.chat_state.phase == RuntimePhase.READY
            )
            await enter_text(pilot, "active")

            def fail_notification(*args, **kwargs):
                del args, kwargs
                raise RuntimeError("notification exploded")

            monkeypatch.setattr(app, "notify", fail_notification)
            service.subscription.publish(
                "test-source",
                runtime_update("test-source"),
            )
            await wait_until(
                pilot, lambda: app.chat_state.active_task_id is None
            )
            result = (app.chat_state.phase, app._exception)
            app.request_shutdown(return_code=0)
            await wait_until(pilot, lambda: service.stopped)
            return result

    phase, exception = asyncio.run(run())

    assert phase == RuntimePhase.READY
    assert exception is None


def test清理汇总subscription和service错误后仍退出(tmp_path):
    async def run():
        service = ControlledService(
            subscription_close_error=RuntimeError("close exploded"),
            stop_error=RuntimeError("stop exploded"),
        )
        app = make_app(service, tmp_path)
        async with app.run_test() as pilot:
            await wait_until(
                pilot, lambda: app.chat_state.phase == RuntimePhase.READY
            )
            app.request_shutdown(return_code=0)
            await wait_until(pilot, lambda: app.return_code == 1)
            return (
                tuple(service.actions),
                service.stopped,
                app._fatal_message,
                app._exception,
            )

    actions, stopped, fatal_message, exception = asyncio.run(run())

    assert "subscription-close" in actions
    assert "service-stop" in actions
    assert stopped is True
    assert "close exploded" in fatal_message
    assert "stop exploded" in fatal_message
    assert exception is None


def test_queue更新失败进入fatal并保留未提交消息(monkeypatch, tmp_path):
    async def fail_queue(self, messages):
        del self, messages
        raise RuntimeError("queue exploded")

    monkeypatch.setattr(QueuePanel, "show_pending", fail_queue)

    async def run():
        service = ControlledService()
        app = make_app(service, tmp_path)
        async with app.run_test() as pilot:
            await wait_until(
                pilot, lambda: app.chat_state.phase == RuntimePhase.READY
            )
            await enter_text(pilot, "keep queued")
            result = (
                tuple(service.submissions),
                app.chat_state.pending_items,
                str(app.query_one(RuntimeStatusBar).render()),
                app._exception,
            )
            app.request_shutdown(return_code=0)
            await wait_until(pilot, lambda: service.stopped)
            return result

    submissions, pending, status, exception = asyncio.run(run())

    assert submissions == ()
    assert pending == ("keep queued",)
    assert "queue rendering" in status
    assert "queue exploded" in status
    assert exception is None


def test_status更新失败进入fatal但保留composer草稿(monkeypatch, tmp_path):
    async def run():
        service = ControlledService()
        app = make_app(service, tmp_path)
        async with app.run_test() as pilot:
            await wait_until(
                pilot, lambda: app.chat_state.phase == RuntimePhase.READY
            )
            composer = app.query_one(PersistentComposer)
            composer.load_text("keep draft")
            status = app.query_one(RuntimeStatusBar)

            def fail_status(*args, **kwargs):
                del args, kwargs
                raise RuntimeError("status exploded")

            monkeypatch.setattr(status, "set_status", fail_status)
            app._refresh_status("trigger failure")
            draft_after_failure = composer.text
            composer.clear_draft()
            await enter_text(pilot, "must not submit")
            result = (
                app.chat_state.phase,
                draft_after_failure,
                app._fatal_message,
                app._exception,
                tuple(service.submissions),
                app.chat_state.pending_items,
            )
            app.request_shutdown(return_code=0)
            await wait_until(pilot, lambda: service.stopped)
            return result

    phase, draft, fatal_message, exception, submissions, pending = asyncio.run(
        run()
    )

    assert phase == RuntimePhase.FAILED
    assert draft == "keep draft"
    assert "status bar update" in fatal_message
    assert "status exploded" in fatal_message
    assert exception is None
    assert submissions == ()
    assert pending == ("must not submit",)


def test未知ui_action进入fatal而不是终止textual消息循环(tmp_path):
    async def run():
        service = ControlledService()
        app = make_app(
            service,
            tmp_path,
            projector_registry=registry_with(
                "test-source", UnknownActionProjector()
            ),
        )
        async with app.run_test() as pilot:
            await wait_until(
                pilot, lambda: app.chat_state.phase == RuntimePhase.READY
            )
            await enter_text(pilot, "active")
            service.subscription.publish(
                "test-source",
                runtime_update("test-source"),
            )
            await wait_until(
                pilot, lambda: app.chat_state.phase == RuntimePhase.FAILED
            )
            result = (app._fatal_message, app._exception)
            app.request_shutdown(return_code=0)
            await wait_until(pilot, lambda: service.stopped)
            return result

    fatal_message, exception = asyncio.run(run())

    assert "UI action routing" in fatal_message
    assert "Unhandled UiAction" in fatal_message
    assert exception is None


@pytest.mark.parametrize(
    ("size", "draft_lines"),
    [
        ((100, 30), 1),
        ((100, 30), 8),
        ((58, 24), 1),
        ((58, 24), 8),
        ((58, 12), 1),
        ((58, 12), 8),
    ],
)
def test_shell区域在宽窄窗口和多行composer下不重叠或越界(
    tmp_path, size, draft_lines
):
    async def run():
        service = ControlledService(block_start=True)
        app = make_app(service, tmp_path)
        async with app.run_test(size=size) as pilot:
            await service.start_entered.wait()
            composer = app.query_one(PersistentComposer)
            composer.load_text(
                "\n".join(f"draft line {index}" for index in range(draft_lines))
            )
            await pilot.pause()
            title = app.query_one("#app-title")
            workspace = app.query_one("#workspace-label")
            composer_shell = app.query_one("#composer-shell")
            status = app.query_one(RuntimeStatusBar)
            regions = (
                title.region,
                workspace.region,
                composer_shell.region,
                status.region,
                app.screen.region,
                workspace.display,
            )
            app.request_shutdown(return_code=0)
            service.start_release.set()
            await wait_until(pilot, lambda: service.stopped)
            return regions

    title, workspace, composer, status, screen, workspace_visible = asyncio.run(
        run()
    )

    if workspace_visible:
        assert title.bottom <= workspace.y
    assert composer.bottom <= status.y
    assert composer.right <= screen.right
    assert status.bottom <= screen.bottom


@pytest.mark.parametrize(
    ("size", "compact_logo", "expected_logo_visible"),
    [
        ((89, 21), False, True),
        ((88, 21), True, False),
        ((100, 20), False, True),
        ((100, 12), False, False),
    ],
)
def test_art_logo仅在宽度不足或极短窗口切换为紧凑标题(
    tmp_path, size, compact_logo, expected_logo_visible
):
    async def run():
        service = ControlledService(block_start=True)
        app = make_app(service, tmp_path)
        async with app.run_test(size=size) as pilot:
            await service.start_entered.wait()
            await pilot.pause()
            result = (
                app.screen.has_class("-compact-logo"),
                app.query_one(".welcome-logo").display,
                app.query_one(".welcome-title").display,
            )
            app.request_shutdown(return_code=0)
            service.start_release.set()
            await wait_until(pilot, lambda: service.stopped)
            return result

    class_enabled, logo_visible, title_visible = asyncio.run(run())

    assert class_enabled is compact_logo
    assert logo_visible is expected_logo_visible
    assert title_visible is not expected_logo_visible


def test_short窗口同时有队列和八行composer时status仍可见(tmp_path):
    async def run():
        service = ControlledService(block_start=True)
        app = make_app(service, tmp_path)
        async with app.run_test(size=(58, 12)) as pilot:
            await service.start_entered.wait()
            await enter_text(pilot, "queued first")
            await enter_text(pilot, "queued second")
            composer = app.query_one(PersistentComposer)
            composer.load_text(
                "\n".join(f"draft line {index}" for index in range(8))
            )
            await pilot.pause()
            conversation = app.query_one("#conversation").region
            queue_panel = app.query_one(QueuePanel).region
            composer_shell = app.query_one("#composer-shell").region
            status = app.query_one(RuntimeStatusBar).region
            screen = app.screen.region
            app.request_shutdown(return_code=0)
            service.start_release.set()
            await wait_until(pilot, lambda: service.stopped)
            return conversation, queue_panel, composer_shell, status, screen

    conversation, queue_panel, composer, status, screen = asyncio.run(run())

    assert conversation.height >= 1
    assert conversation.bottom <= queue_panel.y
    assert queue_panel.bottom <= composer.y
    assert composer.bottom <= status.y
    assert status.bottom <= screen.bottom


def test_pageup与ctrl_end跨焦点滚动且保留composer草稿光标(tmp_path):
    async def run():
        service = ControlledService()
        app = make_app(service, tmp_path)
        async with app.run_test(size=(58, 16)) as pilot:
            await wait_until(
                pilot, lambda: app.chat_state.phase == RuntimePhase.READY
            )
            conversation = app.query_one("#conversation")
            for index in range(30):
                await conversation.append_user_message(
                    f"history {index} with enough content to overflow"
                )
            await pilot.pause()
            composer = app.query_one(PersistentComposer)
            composer.load_text("draft line one\ndraft line two")
            composer.move_cursor((0, 5))
            composer.focus()
            before = (composer.text, composer.cursor_location, app.focused)

            await pilot.press("pageup")
            await pilot.pause()
            after_page_up = (
                conversation.scroll_y,
                conversation.max_scroll_y,
                composer.text,
                composer.cursor_location,
                app.focused,
            )
            await pilot.press("ctrl+end")
            await pilot.pause()
            after_end = (
                conversation.scroll_y,
                conversation.max_scroll_y,
                composer.text,
                composer.cursor_location,
                app.focused,
            )
            app.request_shutdown(return_code=0)
            await wait_until(pilot, lambda: service.stopped)
            return before, after_page_up, after_end

    before, after_page_up, after_end = asyncio.run(run())

    assert after_page_up[0] < after_page_up[1]
    assert after_page_up[2:] == before
    assert after_end[0] == after_end[1]
    assert after_end[2:] == before


def test方向键按焦点分别控制conversation和composer(tmp_path):
    async def run():
        service = ControlledService()
        app = make_app(service, tmp_path)
        async with app.run_test(size=(58, 16)) as pilot:
            await wait_until(
                pilot, lambda: app.chat_state.phase == RuntimePhase.READY
            )
            conversation = app.query_one("#conversation")
            for index in range(30):
                await conversation.append_user_message(
                    f"history {index} with enough content to overflow"
                )
            await pilot.pause()

            conversation.focus()
            before_conversation_up = conversation.scroll_y
            await pilot.press("up")
            await pilot.pause()
            after_conversation_up = conversation.scroll_y

            composer = app.query_one(PersistentComposer)
            composer.load_text("first line\nsecond line")
            composer.move_cursor((1, 3))
            composer.focus()
            conversation_before_composer_up = conversation.scroll_y
            await pilot.press("up")
            await pilot.pause()
            result = (
                before_conversation_up,
                after_conversation_up,
                conversation_before_composer_up,
                conversation.scroll_y,
                composer.cursor_location,
                app.focused,
            )
            app.request_shutdown(return_code=0)
            await wait_until(pilot, lambda: service.stopped)
            return result

    (
        before_conversation_up,
        after_conversation_up,
        conversation_before_composer_up,
        conversation_after_composer_up,
        cursor_after_composer_up,
        focused,
    ) = asyncio.run(run())

    assert after_conversation_up < before_conversation_up
    assert conversation_after_composer_up == conversation_before_composer_up
    assert cursor_after_composer_up == (0, 3)
    assert isinstance(focused, PersistentComposer)
