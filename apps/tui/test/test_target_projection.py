from apps.tui.src.stores.conversation import ConversationProjection
from apps.tui.src.event_pipeline import (
    AppendUserMessage,
    AppendUserCorrection,
    AppendToolStarted,
    CompleteThinking,
)


def test_correction_stays_in_turn_but_outside_execution_card():
    p = ConversationProjection()
    p.apply(AppendUserMessage("t", "first"))
    p.apply(AppendToolStarted("t", "c", "read", "{}"))
    p.apply(AppendUserCorrection("t", "correct", 2))
    assert len(p.turns) == 1
    assert [u.kind for u in p.turns[0].units] == ["user", "run_card", "correction"]
    assert not any(u.kind == "correction" for u in p.turns[0].units[1].children)


def test_complete_thinking_records_completion_for_window_rebuild():
    p = ConversationProjection()
    p.apply(AppendUserMessage("t", "first"))
    p.apply(CompleteThinking("t", 1, "done"))
    thinking = p.turns[0].units[1].children[0]
    assert thinking.completed
