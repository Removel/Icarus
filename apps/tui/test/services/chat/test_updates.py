"""Stream checks at the pure, production UpdateProcessor interface."""

from datetime import UTC, datetime
import pytest
from packages.gateway_protocol import RuntimeUpdateModel
from apps.tui.src.event_pipeline import create_default_projector_registry
from apps.tui.src.services.chat.updates import UpdateProcessor


def update(kind="task.started", *, sequence=None, session_id="s", payload=None):
    return RuntimeUpdateModel(
        workspace_key="w",
        session_id=session_id,
        task_id="t",
        type=kind,
        payload=payload or {},
        sequence=sequence,
        occurred_at=datetime.now(UTC),
    )


def project(processor, record, historical=False):
    actions = processor.project(
        record,
        session_id="s",
        workspace_key="w",
        active_task_id="t",
        historical=historical,
    )
    if actions is not None:
        processor.commit(record)
    return actions


def test_sequence_gap_raises_without_advancing_cursor():
    p = UpdateProcessor(create_default_projector_registry())
    project(p, update(sequence=1))
    with pytest.raises(RuntimeError, match="sequence gap"):
        project(p, update(sequence=3))
    assert p.sequence == 1


def test_inactive_session_and_duplicates_are_ignored():
    p = UpdateProcessor(create_default_projector_registry())
    assert project(p, update(sequence=1, session_id="other")) is None
    assert p.sequence == 0
    project(p, update(sequence=1))
    assert project(p, update(sequence=1)) is None
    assert p.sequence == 1


def test_historical_runtime_status_filtered_and_gaps_allowed():
    p = UpdateProcessor(create_default_projector_registry())
    assert project(p, update(sequence=7), historical=True) == ()
    assert p.sequence == 7


def test_late_delta_after_complete_ignored_but_sequence_consumed():
    p = UpdateProcessor(create_default_projector_registry())
    project(
        p,
        update(
            "assistant.message", sequence=1, payload={"step": 1, "text": "complete"}
        ),
    )
    assert (
        project(
            p,
            update(
                "assistant.text_delta", sequence=2, payload={"step": 1, "text": "late"}
            ),
        )
        is None
    )
    assert p.sequence == 2


def test_reset_clears_completion_and_sequence():
    p = UpdateProcessor(create_default_projector_registry())
    project(
        p,
        update(
            "assistant.message", sequence=1, payload={"step": 1, "text": "complete"}
        ),
    )
    p.reset()
    assert p.sequence == 0 and not p.completed and not p.early
