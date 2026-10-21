from apps.tui.src.event_pipeline import (
    AppendAssistantDelta,
    AppendThinkingDelta,
    AppendToolStarted,
    AppendUserCorrection,
    AppendUserMessage,
    CompleteAssistantMessage,
    CompleteThinking,
    FinishTurn,
    UpdateToolCompleted,
)
from apps.tui.src.widgets.conversation_projection import ConversationProjection


def test_projection只索引顶层用户输入且保留卡片内容():
    projection = ConversationProjection()
    assert projection.apply(AppendUserMessage("task-1", "first"))
    assert projection.apply(AppendThinkingDelta("task-1", 1, "think"))
    assert projection.apply(CompleteThinking("task-1", 1, "thinking"))
    assert projection.apply(AppendUserCorrection("task-1", "correction", 2))
    assert projection.apply(AppendUserMessage("task-2", "second"))
    assert [(turn.task_id, turn.text) for turn in projection.turns] == [
        ("task-1", "first"),
        ("task-2", "second"),
    ]
    first = projection.snapshot_for_turn(0)
    assert first[0].kind == "user"
    assert first[0].text == "first"
    assert first[1].kind == "run_card"
    assert [(item.kind, item.text) for item in first[1].children] == [
        ("thinking", "thinking"),
        ("correction", "correction"),
    ]


def test_projection完整assistant保留在后续工具卡片之前():
    projection = ConversationProjection()
    projection.apply(AppendUserMessage("task-1", "question"))
    projection.apply(AppendAssistantDelta("task-1", "partial", step=1))
    projection.apply(CompleteAssistantMessage("task-1", "complete", step=1))
    projection.apply(AppendToolStarted("task-1", "tool", "read", "{}", step=1))
    projection.apply(FinishTurn("task-1", "completed"))
    units = projection.snapshot_for_turn(0)
    assert [unit.kind for unit in units] == ["user", "assistant", "run_card"]
    assert units[1].text == "complete"
    assert [(item.kind, item.call_id) for item in units[2].children] == [
        ("tool", "tool")
    ]
    assert projection.apply(CompleteAssistantMessage("task-1", "late", step=1))
    assert projection.snapshot_for_turn(0)[1].text == "complete"


def test_projection未完成助手在工具开始时降级为过程():
    projection = ConversationProjection()
    projection.apply(AppendUserMessage("task-1", "question"))
    projection.apply(AppendAssistantDelta("task-1", "partial", step=1))
    projection.apply(AppendToolStarted("task-1", "tool", "read", "{}", step=1))
    units = projection.snapshot_for_turn(0)
    assert [unit.kind for unit in units] == ["user", "run_card"]
    assert [(item.kind, item.text) for item in units[1].children] == [
        ("progress", "partial"),
        ("tool", ""),
    ]


def test_projection相同call_id跨任务独立且缺失start也可完成():
    projection = ConversationProjection()
    projection.apply(AppendUserMessage("task-1", "one"))
    projection.apply(AppendToolStarted("task-1", "same", "read", "{}"))
    projection.apply(AppendUserMessage("task-2", "two"))
    projection.apply(UpdateToolCompleted("task-2", "same", "write", False, "failed"))
    projection.apply(UpdateToolCompleted("task-1", "same", "read", True))
    one = projection.snapshot_for_turn(0)[1].children[0]
    two = projection.snapshot_for_turn(1)[1].children[0]
    assert (one.call_id, one.status) == ("same", "completed")
    assert (two.call_id, two.status) == ("same", "failed")


def test_projection重置与无用户消息前的事件():
    projection = ConversationProjection()
    projection.apply(AppendThinkingDelta("task-1", 1, "before user"))
    assert projection.turns == []
    projection.apply(AppendUserMessage("task-1", "question"))
    assert len(projection.turns) == 1
    projection.reset()
    assert projection.turns == []
    assert projection.units == []


def test_projection_completed_assistant_stays_complete_after_turn_finishes():
    projection = ConversationProjection()
    projection.apply(AppendUserMessage("task", "question"))
    projection.apply(AppendAssistantDelta("task", "hello"))
    projection.apply(CompleteAssistantMessage("task", "hello"))
    projection.apply(FinishTurn("task", "completed"))
    assert projection.snapshot_for_turn(0)[1].completed is True


def test_projection_maintains_sequence_when_past_task_finishes_late():
    projection = ConversationProjection()
    projection.apply(AppendUserMessage("one", "first"))
    projection.apply(AppendUserMessage("two", "second"))
    projection.apply(CompleteAssistantMessage("one", "late"))
    assert [unit.task_id for unit in projection.units] == ["one", "one", "two"]
    assert projection.turn_index("one") == 0
    assert projection.turn_index("two") == 1
