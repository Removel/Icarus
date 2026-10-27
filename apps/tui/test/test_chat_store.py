from apps.tui.src.domain.chat_state import RuntimePhase
from apps.tui.src.event_pipeline import AppendUserMessage, AppendAssistantDelta


def test_store_snapshot_does_not_change_while_stream_advances():
    from apps.tui.src.stores.chat import ChatStore

    store = ChatStore()
    store.apply_actions(
        (AppendUserMessage("t", "hello"), AppendAssistantDelta("t", "first"))
    )
    snapshot = store.capture_window(0, 1)
    store.apply_actions((AppendAssistantDelta("t", " second"),))
    assert snapshot.units[-1].text == "first"
    assert store.conversation.units[-1].text == "first second"
    assert snapshot.revision < store.revision


def test_changes_since_keeps_order_across_status_only_revisions():
    from apps.tui.src.stores.chat import ChatStore

    store = ChatStore()
    first = store.apply_actions((AppendUserMessage("t", "hello"),))
    second = store.apply_actions((AppendAssistantDelta("t", "one"),))
    status = store.changed()
    third = store.apply_actions((AppendAssistantDelta("t", "two"),))
    assert store.changes_since(first.revision) == (second, status, third)
    assert store.changes_since(third.revision) == ()


def test_changes_since_requires_snapshot_after_reset_or_expired_history():
    from apps.tui.src.stores.chat import ChatStore

    store = ChatStore()
    first = store.apply_actions((AppendUserMessage("t", "hello"),))
    store.reset_session(generation=1)
    assert store.changes_since(first.revision) is None
    reset_revision = store.revision
    for _ in range(1024):
        store.changed()
    assert store.changes_since(reset_revision) is None
    assert store.changes_since(store.revision - 1) == (store.last_change,)


def test_viewmodel_uses_unknowns_not_demo_data():
    from apps.tui.src.stores.chat import ChatStore
    from apps.tui.src.viewmodels.chat import build_chat_viewmodel

    store = ChatStore()
    store.interaction.mark_ready()
    vm = build_chat_viewmodel(store)
    assert vm.model_label == "未知"
    assert vm.context_label == "—"
    assert vm.latest_version_label == "未检查"
    assert vm.can_run_session_command
    assert vm.status_kind == RuntimePhase.READY
