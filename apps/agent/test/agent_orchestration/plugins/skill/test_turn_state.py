from dataclasses import FrozenInstanceError

import pytest

from apps.agent.src.agent_orchestration.capability import (
    AgentToolCompletedEvent,
    AgentToolStartedEvent,
)
from apps.agent.src.agent_orchestration.plugins.skill.models import (
    SkillDefinition,
)
from apps.agent.src.agent_orchestration.plugins.skill.turn_state import (
    SkillTurnState,
)
from apps.agent.src.agent_orchestration.plugins.user_input.events import (
    UserInputEvent,
)
from apps.agent.src.agent_orchestration.tools import ToolExecutionResult
from apps.agent.src.model_provider.types import ImagePart, ToolCall


def make_skill(tmp_path, name):
    return SkillDefinition(
        name=name,
        description=f"description {name}",
        path=tmp_path / name / "SKILL.md",
        scope="workspace",
    )


def started(correlation_id, call_id, *, step=1):
    return AgentToolStartedEvent(
        correlation_id=correlation_id,
        step=step,
        tool_call=ToolCall(
            id=call_id,
            name=f"tool-{call_id}",
            arguments={"call_id": call_id},
        ),
    )


def completed(started_event, result):
    return AgentToolCompletedEvent(
        correlation_id=started_event.correlation_id,
        step=started_event.step,
        tool_call=started_event.tool_call,
        result=result,
    )


def test_turn_state保存原始输入和本轮命中skill快照(tmp_path):
    state = SkillTurnState()
    image = ImagePart(url="https://example.com/image.png")
    input_event = UserInputEvent(
        correlation_id="turn-1",
        prompt="build a reusable skill",
        input_images=[image],
    )
    matched = [make_skill(tmp_path, "one"), make_skill(tmp_path, "two")]

    assert state.start(input_event) is True
    assert state.set_matched_skills("turn-1", matched) is True
    matched.clear()

    turn = state.pop("turn-1")
    assert turn is not None
    assert turn.correlation_id == "turn-1"
    assert turn.prompt == input_event.prompt
    assert turn.input_images == (image,)
    assert turn.matched_skills == (
        make_skill(tmp_path, "one"),
        make_skill(tmp_path, "two"),
    )
    assert turn.tool_call_count == 0


def test_turn_state按started顺序保存调用并按call_id回填反序结果():
    state = SkillTurnState()
    state.start(UserInputEvent(correlation_id="turn-1", prompt="run"))
    first = started("turn-1", "first", step=1)
    second = started("turn-1", "second", step=2)
    first_result = ToolExecutionResult(success=True, output="first output")
    second_result = ToolExecutionResult(success=False, error="second failed")

    assert state.record_tool_started(first) is True
    assert state.record_tool_started(second) is True
    assert state.record_tool_completed(completed(second, second_result)) is True
    assert state.record_tool_completed(completed(first, first_result)) is True

    turn = state.pop("turn-1")
    assert turn is not None
    assert turn.tool_call_count == 2
    assert [trace.tool_call.id for trace in turn.tool_calls] == [
        "first",
        "second",
    ]
    assert [trace.step for trace in turn.tool_calls] == [1, 2]
    assert [trace.result for trace in turn.tool_calls] == [
        first_result,
        second_result,
    ]
    assert turn.results_by_call_id == {
        "first": first_result,
        "second": second_result,
    }
    with pytest.raises(TypeError):
        turn.results_by_call_id["third"] = first_result
    with pytest.raises(FrozenInstanceError):
        turn.matched_skills = ()


def test_turn_state弹出记录不受原事件和结果后续修改():
    state = SkillTurnState()
    input_event = UserInputEvent(
        correlation_id="turn-1",
        prompt="run",
        input_images=[ImagePart(url="https://example.com/original.png")],
    )
    state.start(input_event)
    call_event = AgentToolStartedEvent(
        correlation_id="turn-1",
        step=1,
        tool_call=ToolCall(
            id="call-1",
            name="mutable",
            arguments={"nested": {"value": "original"}},
        ),
    )
    result = ToolExecutionResult(
        success=True,
        output={"nested": ["original"]},
    )
    state.record_tool_started(call_event)
    state.record_tool_completed(completed(call_event, result))
    input_event.input_images.append(
        ImagePart(url="https://example.com/late.png")
    )
    call_event.tool_call.arguments["nested"]["value"] = "changed"
    result.output["nested"].append("changed")

    turn = state.pop("turn-1")
    assert turn is not None

    assert len(turn.input_images) == 1
    assert turn.tool_calls[0].tool_call.arguments == {
        "nested": {"value": "original"}
    }
    assert turn.results_by_call_id["call-1"].output == {
        "nested": ("original",)
    }
    with pytest.raises(TypeError):
        turn.tool_calls[0].tool_call.arguments["new"] = "blocked"
    with pytest.raises(TypeError):
        turn.tool_calls[0].tool_call.arguments["nested"]["value"] = (
            "blocked"
        )


def test_turn_state只按started计数且失败结果仍保留():
    state = SkillTurnState()
    state.start(UserInputEvent(correlation_id="turn-1", prompt="run"))

    events = [started("turn-1", f"call-{index}") for index in range(11)]
    for event in events:
        state.record_tool_started(event)
    state.record_tool_completed(
        completed(
            events[-1],
            ToolExecutionResult(success=False, error="tool failed"),
        )
    )

    turn = state.pop("turn-1")
    assert turn is not None
    assert turn.tool_call_count == 11
    assert turn.tool_calls[-1].result == ToolExecutionResult(
        success=False,
        error="tool failed",
    )


def test_turn_state重复call_id每次计数且completed依次回填未完成trace():
    state = SkillTurnState()
    state.start(UserInputEvent(correlation_id="turn-1", prompt="run"))
    original = started("turn-1", "duplicate", step=1)
    duplicate = started("turn-1", "duplicate", step=2)

    assert state.record_tool_started(original) is True
    assert state.record_tool_started(duplicate) is True
    first_result = ToolExecutionResult(success=True, output={"order": 1})
    second_result = ToolExecutionResult(success=False, error="second")
    assert state.record_tool_completed(completed(duplicate, first_result)) is True
    assert state.record_tool_completed(completed(original, second_result)) is True
    assert state.record_tool_completed(completed(original, second_result)) is False

    turn = state.pop("turn-1")
    assert turn is not None
    assert turn.tool_call_count == 2
    assert turn.started_event_count == 2
    assert [trace.sequence_index for trace in turn.tool_calls] == [0, 1]
    assert [trace.step for trace in turn.tool_calls] == [1, 2]
    assert [trace.result for trace in turn.tool_calls] == [
        ToolExecutionResult(
            success=True,
            output={"order": 1},
        ),
        second_result,
    ]
    assert turn.results_by_call_id["duplicate"] == second_result


def test_turn_state未知无id和迟到事件安全忽略():
    state = SkillTurnState()
    missing_id_input = UserInputEvent(prompt="missing id")
    unknown_started = started("unknown", "call-1")
    missing_id_started = started(None, "call-2")

    assert state.start(missing_id_input) is False
    assert state.set_matched_skills(None, []) is False
    assert state.set_matched_skills("unknown", []) is False
    assert state.record_tool_started(unknown_started) is False
    assert state.record_tool_started(missing_id_started) is False
    assert (
        state.record_tool_completed(
            completed(
                unknown_started,
                ToolExecutionResult(success=True, output="late"),
            )
        )
        is False
    )
    assert state.pop(None) is None
    assert state.pop("unknown") is None
    assert state.discard(None) is None
    assert state.discard("unknown") is None

    state.start(UserInputEvent(correlation_id="finished", prompt="done"))
    call = started("finished", "call-3")
    assert state.record_tool_started(call) is True
    assert state.pop("finished") is not None
    assert state.record_tool_started(call) is False
    assert (
        state.record_tool_completed(
            completed(call, ToolExecutionResult(success=True, output="late"))
        )
        is False
    )


def test_turn_state未知call完成事件不会改写已记录轨迹():
    state = SkillTurnState()
    state.start(UserInputEvent(correlation_id="turn-1", prompt="run"))
    known = started("turn-1", "known")
    unknown = started("turn-1", "unknown")
    state.record_tool_started(known)

    assert (
        state.record_tool_completed(
            completed(unknown, ToolExecutionResult(success=True, output="bad"))
        )
        is False
    )

    turn = state.pop("turn-1")
    assert turn is not None
    assert len(turn.tool_calls) == 1
    assert turn.tool_calls[0].tool_call.id == "known"
    assert turn.tool_calls[0].result is None


def test_turn_state重复begin替换旧轮且多轮互相隔离():
    state = SkillTurnState()
    state.start(UserInputEvent(correlation_id="same", prompt="old"))
    state.record_tool_started(started("same", "old-call"))
    state.start(UserInputEvent(correlation_id="other", prompt="other"))

    assert (
        state.start(UserInputEvent(correlation_id="same", prompt="new"))
        is False
    )
    state.record_tool_started(started("same", "new-call"))
    state.record_tool_started(started("other", "other-call"))

    same = state.pop("same")
    other = state.discard("other")
    assert same is not None
    assert same.prompt == "new"
    assert [trace.tool_call.id for trace in same.tool_calls] == ["new-call"]
    assert other is not None
    assert [trace.tool_call.id for trace in other.tool_calls] == [
        "other-call"
    ]
    assert state.pop("same") is None
    assert state.discard("other") is None


@pytest.mark.parametrize("correlation_id", [None, "", "   "])
def test_turn_state空白correlation_id统一忽略(correlation_id):
    state = SkillTurnState()
    input_event = UserInputEvent(
        correlation_id=correlation_id,
        prompt="missing id",
    )
    call_event = started(correlation_id, "call-1")

    assert state.start(input_event) is False
    assert state.set_matched_skills(correlation_id, []) is False
    assert state.record_tool_started(call_event) is False
    assert (
        state.record_tool_completed(
            completed(
                call_event,
                ToolExecutionResult(success=True, output="ignored"),
            )
        )
        is False
    )
    assert state.pop(correlation_id) is None
    assert state.discard(correlation_id) is None
