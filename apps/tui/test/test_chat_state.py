import pytest

from apps.tui.src.chat_state import (
    ChatState,
    InterruptAction,
    RuntimePhase,
)


def ready_state() -> ChatState:
    state = ChatState()
    state.mark_ready()
    return state


def test_starting阶段允许排队但不允许调度():
    state = ChatState()

    state.enqueue("first")

    assert state.pending_items == ("first",)
    assert state.can_dispatch is False
    assert state.begin_dispatch() is None


def test_dispatch成功前保留队首并防止重复dispatch():
    state = ready_state()
    state.enqueue("first")
    state.enqueue("second")

    assert state.begin_dispatch() == "first"
    assert state.pending_items == ("first", "second")
    assert state.begin_dispatch() is None

    accepted_message = state.accept_dispatch("task-1")

    assert accepted_message == "first"
    assert state.pending_items == ("second",)
    assert state.active_task_id == "task-1"
    assert state.phase == RuntimePhase.RUNNING


def test_dispatch失败保留完整队首并暂停自动重试():
    state = ready_state()
    message = "  first\n    indented  "
    state.enqueue(message)
    state.begin_dispatch()

    state.fail_dispatch()

    assert state.pending_items == (message,)
    assert state.dispatch_in_progress is False
    assert state.phase == RuntimePhase.FAILED
    assert state.can_dispatch is False


def test正常消费FIFO且撤回LIFO并保留原文():
    state = ready_state()
    first = "第一条"
    second = "second\n  缩进 🚀"
    state.enqueue(first)
    state.enqueue(second)

    state.begin_dispatch()
    assert state.accept_dispatch("task-1") == first
    assert state.pop_pending_tail() == second
    assert state.pending_items == ()


def test只有匹配当前task的终态能结束并恢复调度():
    state = ready_state()
    state.enqueue("first")
    state.enqueue("second")
    state.begin_dispatch()
    state.accept_dispatch("task-1")

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
    state.begin_dispatch()

    assert state.interrupt_action("") == InterruptAction.RESTORE_PENDING

    state.pending.clear()
    assert (
        state.interrupt_action("")
        == InterruptAction.NOTIFY_CANCEL_UNAVAILABLE
    )


def test拒绝空白队列项且非法接受给出明确错误():
    state = ready_state()

    with pytest.raises(ValueError, match="cannot be empty"):
        state.enqueue("  \n")
    with pytest.raises(RuntimeError, match="No dispatch"):
        state.accept_dispatch("task-1")
