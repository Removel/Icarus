import pytest

from apps.tui.src.chat_state import (
    ChatState,
    DispatchReservation,
    InterruptAction,
    RuntimePhase,
)
from apps.tui.src.submission import DraftImage, PendingMessage


def ready_state() -> ChatState:
    state = ChatState()
    state.mark_ready()
    return state


def test_starting阶段允许排队但不允许调度():
    state = ChatState()

    state.enqueue("first")

    assert state.pending_items == ("first",)
    assert state.can_dispatch is False
    assert state.begin_dispatch(1) is None


def test_dispatch成功前保留队首并防止重复dispatch():
    state = ready_state()
    state.enqueue("first")
    state.enqueue("second")

    reservation = state.begin_dispatch(1)
    assert reservation is not None
    assert reservation.message == PendingMessage("first")
    assert reservation.mode == "submit"
    assert reservation.task_id is None
    assert state.pending_items == ("first", "second")
    assert state.begin_dispatch(1) is None

    accepted_message = state.accept_submit(reservation, "task-1")

    assert accepted_message == PendingMessage("first")
    assert state.pending_items == ("second",)
    assert state.active_task_id == "task-1"
    assert state.phase == RuntimePhase.RUNNING


def test_dispatch阻塞保留完整队首并暂停自动重试():
    state = ready_state()
    message = "  first\n    indented  "
    state.enqueue(message)
    reservation = state.begin_dispatch(1)
    assert reservation is not None

    state.block_dispatch(reservation, "invalid resource")

    assert state.pending_items == (message,)
    assert state.dispatch_in_progress is False
    assert state.phase == RuntimePhase.READY
    assert state.can_dispatch is False


def test正常消费FIFO且撤回LIFO并保留原文():
    state = ready_state()
    first = "第一条"
    second = "second\n  缩进 🚀"
    state.enqueue(first)
    state.enqueue(second)

    reservation = state.begin_dispatch(1)
    assert reservation is not None
    assert state.accept_submit(reservation, "task-1") == PendingMessage(first)
    assert state.pop_pending_tail() == PendingMessage(second)
    assert state.pending_items == ()


def test图片附件随消息排队提交和撤回(tmp_path):
    state = ready_state()
    image = DraftImage("image1", tmp_path / "clipboard.png")
    submission = PendingMessage("查看 [#image1]", (image,))
    state.enqueue(submission)

    assert state.pending_items == ("查看 [#image1]",)
    assert state.pending_messages == (submission,)
    reservation = state.begin_dispatch(1)
    assert reservation is not None
    assert reservation.message is submission

    state.release_dispatch(reservation)
    assert state.pop_pending_tail() is submission


def test只有匹配当前task的终态能结束并恢复调度():
    state = ready_state()
    state.enqueue("first")
    state.enqueue("second")
    reservation = state.begin_dispatch(1)
    assert reservation is not None
    state.accept_submit(reservation, "task-1")

    assert state.finish_active("other") is False
    assert state.active_task_id == "task-1"
    assert state.finish_active("task-1") is True
    assert state.active_task_id is None
    assert state.phase == RuntimePhase.READY
    assert state.can_dispatch is True
    assert state.finish_active("task-1") is False


@pytest.mark.parametrize(
    ("draft", "queued", "active", "expected"),
    [
        ("draft", ["queued"], "task-1", InterruptAction.CLEAR_DRAFT),
        ("   ", ["queued"], "task-1", InterruptAction.CLEAR_DRAFT),
        ("", ["queued"], "task-1", InterruptAction.RESTORE_PENDING),
        ("", [], "task-1", InterruptAction.CANCEL_ACTIVE),
        ("", [], None, InterruptAction.EXIT),
    ],
)
def test_ctrl_c每次只选择最高优先级动作(draft, queued, active, expected):
    state = ready_state()
    for message in queued:
        state.enqueue(message)
    state.active_task_id = active
    if active is not None:
        state.phase = RuntimePhase.RUNNING

    assert state.interrupt_action(draft) == expected


def test_dispatch握手也被视为运行中不可退出():
    state = ready_state()
    state.enqueue("first")
    state.begin_dispatch(1)

    assert (
        state.interrupt_action("")
        == InterruptAction.NOTIFY_CANCEL_UNAVAILABLE
    )

    assert state.pop_pending_tail() is None


def test_session命令只在完全空闲时允许():
    state = ready_state()
    assert state.can_run_session_command is True

    state.enqueue("queued")
    assert state.can_run_session_command is False
    state.pending.clear()

    state.enqueue("sending")
    state.begin_dispatch(1)
    assert state.can_run_session_command is False


def test_running队列动态保留为steer且完成竞争不改变reservation():
    state = ready_state()
    state.active_task_id = "task-1"
    state.phase = RuntimePhase.RUNNING
    message = PendingMessage("correction")
    state.enqueue(message)

    reservation = state.begin_dispatch(3)

    assert reservation == DispatchReservation(
        message=message,
        mode="steer",
        task_id="task-1",
        attempt_epoch=3,
    )
    assert state.finish_active("task-1") is True
    assert state.dispatch_reservation is reservation
    assert state.accept_steer(reservation) is message
    assert state.phase == RuntimePhase.READY


def test_late_steer释放后在idle重新选择submit():
    state = ready_state()
    state.active_task_id = "task-1"
    state.phase = RuntimePhase.RUNNING
    state.enqueue("keep me")
    first = state.begin_dispatch(1)
    assert first is not None and first.mode == "steer"

    state.finish_active("task-1")
    state.release_dispatch(first)
    second = state.begin_dispatch(1)

    assert second is not None
    assert second.mode == "submit"
    assert second.message is first.message


def test_unknown结果只在更新连接代次后以相同reservation重试():
    state = ready_state()
    state.active_task_id = "task-1"
    state.phase = RuntimePhase.RUNNING
    message = PendingMessage("retry me")
    state.enqueue(message)
    first = state.begin_dispatch(4)
    assert first is not None

    unknown = state.mark_dispatch_outcome_unknown(first)
    state.finish_active("task-1")
    with pytest.raises(RuntimeError, match="newer connection"):
        state.begin_dispatch_retry(unknown, 4)
    retry = state.begin_dispatch_retry(unknown, 5)

    assert retry.message is message
    assert retry.mode == "steer"
    assert retry.task_id == "task-1"
    assert retry.attempt_epoch == 5
    assert retry.outcome_unknown is False


def test发送中只允许撤回未锁定队尾():
    state = ready_state()
    first = PendingMessage("first")
    second = PendingMessage("second")
    state.enqueue(first)
    state.begin_dispatch(1)

    assert state.pop_pending_tail() is None
    state.enqueue(second)
    assert state.pop_pending_tail() is second
    assert state.pop_pending_tail() is None
    assert state.can_run_session_command is False


def test_switching阶段阻止dispatch和session命令():
    state = ready_state()
    state.begin_switching()

    assert state.phase == RuntimePhase.SWITCHING
    assert state.can_dispatch is False
    assert state.can_run_session_command is False


def test拒绝空白队列项且非法接受给出明确错误():
    state = ready_state()

    with pytest.raises(ValueError, match="cannot be empty"):
        state.enqueue("  \n")
    fake = DispatchReservation(PendingMessage("fake"), "submit", None, 1)
    with pytest.raises(RuntimeError, match="stale"):
        state.accept_submit(fake, "task-1")
