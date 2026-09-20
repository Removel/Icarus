import pytest

from apps.agent.src.agent_orchestration.plugins.blackboard.message_history import (
    MessageHistoryError,
    close_interrupted_run,
    project_legacy_history,
    validate_run_messages,
)
from apps.agent.src.model_provider.types import Message, TextPart, ToolCall


def user(text: str) -> Message:
    return Message("user", [TextPart(text)])


def assistant(text: str) -> Message:
    return Message("assistant", [TextPart(text)])


def tool_group():
    return [
        Message(
            "assistant",
            [TextPart("checking")],
            tool_calls=[
                ToolCall("call-a", "read", {"path": "a"}),
                ToolCall("call-b", "read", {"path": "b"}),
            ],
        ),
        Message("tool", [TextPart("a")], tool_call_id="call-a"),
        Message("tool", [TextPart("b")], tool_call_id="call-b"),
    ]


def test_validate_run_messages接受完整tool_group和steer():
    messages = [
        user("request"),
        *tool_group(),
        user("<user_correction>only tests</user_correction>"),
        assistant("done"),
    ]

    assert validate_run_messages(messages, require_final_assistant=True) == tuple(
        messages
    )


@pytest.mark.parametrize(
    "messages",
    [
        [Message("system", [TextPart("system")]), user("request")],
        [user("request"), Message("tool", [TextPart("x")], tool_call_id="x")],
        [user("request"), *tool_group()[:-1]],
        [user("request"), *tool_group()[0:1], tool_group()[2]],
    ],
)
def test_validate_run_messages拒绝非法tool结构(messages):
    with pytest.raises(MessageHistoryError):
        validate_run_messages(messages, require_final_assistant=False)


def test_close_interrupted_run保留完整tool_group并补assistant():
    messages = [user("request"), *tool_group()]

    closed = close_interrupted_run(messages)

    assert closed[:-1] == tuple(messages)
    assert closed[-1] == assistant("Operation interrupted.")
    assert validate_run_messages(closed, require_final_assistant=True) == closed


def test_close_interrupted_run已有普通assistant时不重复闭合():
    messages = [user("request"), assistant("partial but complete message")]

    assert close_interrupted_run(messages) == tuple(messages)


def test_project_legacy_history修复副本但不修改原历史():
    incomplete_call = Message(
        "assistant", [], tool_calls=[ToolCall("missing", "read", {})]
    )
    original = [
        user("first"),
        user("second"),
        assistant("answer"),
        Message("tool", [TextPart("orphan")], tool_call_id="orphan"),
        incomplete_call,
        user("unfinished"),
    ]

    projected = project_legacy_history(original)

    assert projected == [
        Message("user", [TextPart("first"), TextPart("\n\n"), TextPart("second")]),
        assistant("answer"),
    ]
    assert original[-2] is incomplete_call
    assert original[-1] == user("unfinished")


def test_project_legacy_history保留以完整tool_group结束的旧尾部():
    old_group = tool_group()
    original = [user("completed"), assistant("answer"), user("unfinished"), *old_group]

    projected = project_legacy_history(original)

    assert projected == original
    assert original[-1].role == "tool"


def test_project_legacy_history裁掉未配对call并保留完整结果():
    original = [
        user("request"),
        Message(
            "assistant", [TextPart("checking")],
            tool_calls=[
                ToolCall("call-a", "read", {"path": "a"}),
                ToolCall("call-b", "read", {"path": "b"}),
            ],
        ),
        Message("tool", [TextPart("a")], tool_call_id="call-a"),
        assistant("continued"),
    ]

    projected = project_legacy_history(original)

    assert projected[1].tool_calls == [
        ToolCall("call-a", "read", {"path": "a"})
    ]
    assert projected[2].tool_call_id == "call-a"
    assert projected[-1] == assistant("continued")


def test_project_legacy_history丢弃重复tool_call_id的后一个group():
    first_group = tool_group()[:2]
    repeated = [
        Message(
            "assistant", [],
            tool_calls=[ToolCall("call-a", "read", {"path": "again"})],
        ),
        Message("tool", [TextPart("again")], tool_call_id="call-a"),
    ]

    projected = project_legacy_history(
        [
            user("request"),
            *first_group,
            assistant("first done"),
            user("next"),
            *repeated,
            assistant("done"),
        ]
    )

    assert sum(len(message.tool_calls) for message in projected) == 1
    assert all(
        "again" not in part.text
        for message in projected
        for part in message.content
        if isinstance(part, TextPart)
    )


def test_project_legacy_history合并连续assistant并保留tool_group():
    group = tool_group()
    original = [user("request"), assistant("first"), *group, assistant("done")]

    projected = project_legacy_history(original)

    assert [message.role for message in projected] == [
        "user", "assistant", "tool", "tool", "assistant"
    ]
    assert projected[1].tool_calls == group[0].tool_calls
    assert projected[1].content == [
        TextPart("first"), TextPart("\n\n"), TextPart("checking")
    ]
