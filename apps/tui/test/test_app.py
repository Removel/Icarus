import asyncio
from collections.abc import Callable

import pytest
from textual.widgets import Static

from apps.agent.src.agent_orchestration.capability import AgentTextDeltaEvent
from apps.agent.src.agent_orchestration.plugins import (
    InputAccepted,
    InputFinishedEvent,
    InputQueuedEvent,
)
from apps.tui.src.app import IcarusTextualApp
from apps.tui.src.chat_state import RuntimePhase
from apps.tui.src.widgets import (
    AssistantMessage,
    PersistentComposer,
    QueuePanel,
    RuntimeStatusBar,
)


class ControlledSubscription:
    def __init__(self, actions) -> None:
        self.actions = actions
        self.queue = asyncio.Queue()
        self.closed = False

    async def next_event(self):
        item = await self.queue.get()
        if item is None:
            raise RuntimeError("subscription is closed")
        return item

    def publish(self, source, event) -> None:
        self.queue.put_nowait((source, event))

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        self.actions.append("subscription-close")
        self.queue.put_nowait(None)


class ControlledService:
    def __init__(
        self,
        *,
        block_start: bool = False,
        publish_queued_before_return: bool = False,
        submit_error: BaseException | None = None,
        start_error: BaseException | None = None,
        subscribe_error: BaseException | None = None,
    ) -> None:
        self.actions = []
        self.subscription = ControlledSubscription(self.actions)
        self.start_entered = asyncio.Event()
        self.start_release = asyncio.Event()
        if not block_start:
            self.start_release.set()
        self.publish_queued_before_return = publish_queued_before_return
        self.submit_error = submit_error
        self.start_error = start_error
        self.subscribe_error = subscribe_error
        self.submissions = []
        self.session_id = "test-session"
        self.stopped = False

    async def start(self) -> None:
        self.actions.append("service-start")
        self.start_entered.set()
        await self.start_release.wait()
        if self.start_error is not None:
            raise self.start_error
        self.actions.append("service-started")

    def subscribe_events(self):
        self.actions.append("subscribe")
        if self.subscribe_error is not None:
            raise self.subscribe_error
        return self.subscription

    async def submit(self, prompt, input_images=None):
        del input_images
        task_id = f"task-{len(self.submissions) + 1}"
        self.actions.append(f"submit:{prompt}")
        self.submissions.append(prompt)
        if self.submit_error is not None:
            raise self.submit_error
        if self.publish_queued_before_return:
            self.subscription.publish(
                "user-input",
                InputQueuedEvent(
                    correlation_id=task_id,
                    task_id=task_id,
                    queue_position=0,
                ),
            )
            await asyncio.sleep(0)
        return InputAccepted(task_id=task_id, queue_position=0)

    async def stop(self, timeout=30) -> None:
        del timeout
        self.actions.append("service-stop")
        self.stopped = True
        self.subscription.close()


def make_app(service: ControlledService, workspace_path) -> IcarusTextualApp:
    async def runtime_factory():
        service.actions.append("factory")
        return service

    return IcarusTextualApp(
        runtime_factory=runtime_factory,
        workspace_path=workspace_path,
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


def finish_event(task_id: str, status="completed") -> InputFinishedEvent:
    return InputFinishedEvent(
        correlation_id=task_id,
        task_id=task_id,
        status=status,
    )


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


def test_factory失败前保持静默且提交后保留队首并显示错误(tmp_path):
    async def run():
        async def failing_factory():
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
                app.projector_registry.unrelated_event_count,
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
                AgentTextDeltaEvent(
                    correlation_id="task-1",
                    step=1,
                    text="**streaming**",
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


def test_ctrl_c依次清草稿撤回队尾提示不可取消并在空闲退出(tmp_path):
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
            await pilot.pause()
            running_status = str(app.query_one(RuntimeStatusBar).render())
            still_active = app.chat_state.active_task_id

            service.subscription.publish("user-input", finish_event("task-1"))
            await wait_until(
                pilot, lambda: app.chat_state.active_task_id is None
            )
            await pilot.press("ctrl+c")
            await wait_until(pilot, lambda: service.stopped)
            return (
                after_clear,
                after_restore,
                after_second_clear,
                running_status,
                still_active,
                app.return_code,
            )

    (
        after_clear,
        after_restore,
        after_second_clear,
        running_status,
        still_active,
        return_code,
    ) = asyncio.run(run())

    assert after_clear == ("", ("second\nline",))
    assert after_restore == ("second\nline", (1, 4), ())
    assert after_second_clear == ""
    assert "cannot be cancelled" in running_status
    assert still_active == "task-1"
    assert return_code == 0


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
