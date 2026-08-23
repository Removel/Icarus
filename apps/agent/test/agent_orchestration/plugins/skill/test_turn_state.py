from dataclasses import FrozenInstanceError
import json

import pytest

from apps.agent.src.agent_orchestration.plugins.skill.models import (
    SkillDefinition,
)
from apps.agent.src.agent_orchestration.plugins.skill.turn_state import (
    SkillTurnState,
    ToolTrajectoryError,
    tool_call_count_from_messages,
    tool_traces_from_messages,
)
from apps.agent.src.agent_orchestration.plugins.user_input.events import (
    UserInputEvent,
)
from apps.agent.src.agent_orchestration.tools import ToolExecutionResult
from apps.agent.src.model_provider.types import (
    ImagePart,
    Message,
    TextPart,
    ToolCall,
)


def make_skill(tmp_path, name):
    return SkillDefinition(
        name=name,
        description=f"description {name}",
        path=tmp_path / name / "SKILL.md",
        scope="workspace",
    )


def result_message(call_id, result):
    return Message(
        "tool",
        [TextPart(json.dumps(result.as_dict()))],
        tool_call_id=call_id,
    )


def completed_messages(*, calls=()):
    messages = [Message("user", [TextPart("current turn")])]
    for call, result in calls:
        messages.append(Message("assistant", [], tool_calls=[call]))
        messages.append(result_message(call.id, result))
    messages.append(Message("assistant", [TextPart("done")]))
    return messages


def test_turn_state保存原始输入和本轮命中skill快照(tmp_path):
    state = SkillTurnState()
    image = ImagePart(url="https://example.com/image.png")
    input_event = UserInputEvent(
        task_id="turn-1",
        prompt="build a reusable skill",
        input_images=[image],
    )
    matched = [make_skill(tmp_path, "one"), make_skill(tmp_path, "two")]

    assert state.start(input_event) is True
    assert state.set_matched_skills("turn-1", matched) is True
    matched.clear()

    turn = state.pop_completed("turn-1", completed_messages())
    assert turn is not None
    assert turn.task_id == "turn-1"
    assert turn.prompt == input_event.prompt
    assert turn.input_images == (image,)
    assert turn.matched_skills == (
        make_skill(tmp_path, "one"),
        make_skill(tmp_path, "two"),
    )
    assert turn.tool_call_count == 0
    with pytest.raises(FrozenInstanceError):
        turn.matched_skills = ()


def test_turn_state从完整messages恢复当前轮step与成功失败工具结果():
    state = SkillTurnState()
    state.start(UserInputEvent(task_id="turn-1", prompt="run"))
    first = ToolCall(id="first", name="read", arguments={"path": "a"})
    second = ToolCall(
        id="second", name="bash", arguments={"command": "x"}
    )
    messages = [
        Message("user", [TextPart("old turn")]),
        Message("assistant", [TextPart("old answer")]),
        *completed_messages(
            calls=(
                (first, ToolExecutionResult(success=True, output="ok")),
                (second, ToolExecutionResult(success=False, error="bad")),
            )
        ),
    ]

    turn = state.pop_completed("turn-1", messages)

    assert turn is not None
    assert [trace.step for trace in turn.tool_calls] == [1, 2]
    assert [trace.tool_call for trace in turn.tool_calls] == [first, second]
    assert turn.tool_calls[0].result == ToolExecutionResult(
        success=True, output="ok"
    )
    assert turn.tool_calls[1].result == ToolExecutionResult(
        success=False, error="bad"
    )


def test完整messages重复call_id按出现顺序关联结果():
    first = ToolCall(id="duplicate", name="read", arguments={"order": 1})
    second = ToolCall(
        id="duplicate", name="read", arguments={"order": 2}
    )
    traces = tool_traces_from_messages(
        completed_messages(
            calls=(
                (first, ToolExecutionResult(success=True, output="first")),
                (second, ToolExecutionResult(success=False, error="second")),
            )
        )
    )

    assert [trace.sequence_index for trace in traces] == [0, 1]
    assert [trace.tool_call.arguments["order"] for trace in traces] == [1, 2]
    assert traces[0].result == ToolExecutionResult(
        success=True, output="first"
    )
    assert traces[1].result == ToolExecutionResult(
        success=False, error="second"
    )


def test完整messages可只统计工具数量而不解析结果正文():
    calls = [
        ToolCall(id=f"call-{index}", name="read", arguments={})
        for index in range(11)
    ]
    messages = [Message("user", [TextPart("current")])]
    for call in calls:
        messages.append(Message("assistant", [], tool_calls=[call]))
        messages.append(
            Message(
                "tool",
                [TextPart("not-json-and-potentially-large")],
                tool_call_id=call.id,
            )
        )

    assert tool_call_count_from_messages(messages) == 11
    with pytest.raises(ToolTrajectoryError, match="not valid JSON"):
        tool_traces_from_messages(messages)


def test显式task起点不会被运行中context截断工具轨迹():
    first = ToolCall(id="first", name="read", arguments={})
    second = ToolCall(id="second", name="bash", arguments={})
    messages = [
        Message("user", [TextPart("old turn")]),
        Message("assistant", [TextPart("old answer")]),
        Message("user", [TextPart("current turn")]),
        Message("assistant", [], tool_calls=[first]),
        result_message(first.id, ToolExecutionResult(success=True, output="first")),
        Message("user", [TextPart("<runtime_context>extra</runtime_context>")]),
        Message("assistant", [], tool_calls=[second]),
        result_message(second.id, ToolExecutionResult(success=True, output="second")),
        Message("assistant", [TextPart("done")]),
    ]

    traces = tool_traces_from_messages(messages, task_message_start=2)

    assert [trace.tool_call.id for trace in traces] == ["first", "second"]
    assert tool_call_count_from_messages(messages, task_message_start=2) == 2


def test完整messages轨迹快照不受原tool_call后续修改():
    call = ToolCall(
        id="call-1",
        name="read",
        arguments={"nested": {"value": "original"}},
    )
    traces = tool_traces_from_messages(
        completed_messages(
            calls=((call, ToolExecutionResult(success=True, output={"ok": 1})),)
        )
    )
    call.arguments["nested"]["value"] = "changed"

    assert traces[0].tool_call.arguments == {
        "nested": {"value": "original"}
    }
    with pytest.raises(TypeError):
        traces[0].tool_call.arguments["nested"]["value"] = "blocked"


@pytest.mark.parametrize(
    ("messages", "message"),
    [
        ([Message("assistant", [TextPart("no user")])], "no user"),
        (
            [
                Message("user", [TextPart("current")]),
                Message(
                    "assistant",
                    [],
                    tool_calls=[ToolCall(id="missing", name="read", arguments={})],
                ),
            ],
            "incomplete",
        ),
        (
            [
                Message("user", [TextPart("current")]),
                Message(
                    "tool",
                    [TextPart('{"success":true}')],
                    tool_call_id="unknown",
                ),
            ],
            "no unfinished ToolCall",
        ),
    ],
)
def test完整messages无效工具轨迹fail_closed(messages, message):
    with pytest.raises(ToolTrajectoryError, match=message):
        tool_traces_from_messages(messages)


def test_turn_state失败清理重复开始与多轮隔离():
    state = SkillTurnState()
    state.start(UserInputEvent(task_id="same", prompt="old"))
    state.start(UserInputEvent(task_id="other", prompt="other"))

    assert (
        state.start(UserInputEvent(task_id="same", prompt="new"))
        is False
    )
    same = state.pop_completed("same", completed_messages())
    assert same is not None
    assert same.prompt == "new"
    assert state.discard("other") is True
    assert state.discard("other") is False
    assert state.pop_completed("same", completed_messages()) is None


@pytest.mark.parametrize("task_id", [None, "", "   "])
def test_turn_state空白task_id统一忽略(task_id):
    state = SkillTurnState()
    input_event = UserInputEvent(
        task_id=task_id,
        prompt="missing id",
    )

    assert state.start(input_event) is False
    assert state.set_matched_skills(task_id, []) is False
    assert state.pop_completed(task_id, completed_messages()) is None
    assert state.discard(task_id) is False
