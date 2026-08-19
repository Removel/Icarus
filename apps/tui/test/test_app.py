import asyncio
from collections.abc import Callable

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
    ) -> None:
        self.actions = []
        self.subscription = ControlledSubscription(self.actions)
        self.start_entered = asyncio.Event()
        self.start_release = asyncio.Event()
        if not block_start:
            self.start_release.set()
        self.publish_queued_before_return = publish_queued_before_return
        self.submit_error = submit_error
        self.submissions = []
        self.session_id = "test-session"
        self.stopped = False

    async def start(self) -> None:
        self.actions.append("service-start")
        self.start_entered.set()
        await self.start_release.wait()
        self.actions.append("service-started")

    def subscribe_events(self):
        self.actions.append("subscribe")
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
        app = IcarusTextualApp(service=service, workspace_path=tmp_path)
        async with app.run_test() as pilot:
            await wait_until(
                pilot, lambda: app.chat_state.phase == RuntimePhase.READY
            )
            focused = app.focused
            app.request_shutdown(return_code=0)
            await wait_until(pilot, lambda: service.stopped)
        return service, app, focused

    service, app, focused = asyncio.run(run())

    assert service.actions.index("service-started") < service.actions.index(
        "subscribe"
    )
    assert isinstance(focused, PersistentComposer)
    assert service.stopped is True
    assert app.return_code == 0


def test_starting期间可排队且ready后自动提交(tmp_path):
    async def run():
        service = ControlledService(block_start=True)
        app = IcarusTextualApp(service=service, workspace_path=tmp_path)
        async with app.run_test() as pilot:
            await service.start_entered.wait()
            await enter_text(pilot, "queued while starting")
            assert app.chat_state.pending_items == ("queued while starting",)
            assert service.submissions == []

            service.start_release.set()
            await wait_until(pilot, lambda: bool(service.submissions))
            state = (
                tuple(service.submissions),
                app.chat_state.active_task_id,
                app.chat_state.pending_items,
            )
            app.request_shutdown(return_code=0)
            await wait_until(pilot, lambda: service.stopped)
            return state

    submissions, active_task_id, pending = asyncio.run(run())

    assert submissions == ("queued while starting",)
    assert active_task_id == "task-1"
    assert pending == ()


def test运行中输入按FIFO排队并在finish后每次只提交一条(tmp_path):
    async def run():
        service = ControlledService()
        app = IcarusTextualApp(service=service, workspace_path=tmp_path)
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
        app = IcarusTextualApp(service=service, workspace_path=tmp_path)
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
        app = IcarusTextualApp(service=service, workspace_path=tmp_path)
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
        app = IcarusTextualApp(service=service, workspace_path=tmp_path)
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
        app = IcarusTextualApp(service=service, workspace_path=tmp_path)
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
        app = IcarusTextualApp(service=service, workspace_path=tmp_path)
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
