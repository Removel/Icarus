from datetime import UTC, datetime, timedelta

import pytest

from apps.agent.src.agent_orchestration.plugins.skill.jobs import SkillJob


def make_job(*, operation="produce", scope="workspace", now=None):
    return SkillJob.create(
        job_id="job-1",
        operation=operation,
        target_name="sample",
        scope=scope,
        task_id="task-1",
        run_id="run-1",
        step=2,
        now=now,
    )


def test_job_valid_state_machine_and_json_round_trip(tmp_path):
    created = datetime(2026, 8, 24, tzinfo=UTC)
    running_at = created + timedelta(seconds=1)
    finished_at = created + timedelta(seconds=2)
    job = make_job(now=created)

    running = job.transition("running", now=running_at)
    succeeded = running.transition(
        "succeeded", path=tmp_path / "SKILL.md", now=finished_at
    )
    notified = succeeded.with_notification_request("event-1").with_notification_status("accepted")
    restored = SkillJob.from_dict(notified.to_dict())

    assert restored == notified
    assert restored.is_terminal is True
    assert restored.path == str(tmp_path / "SKILL.md")
    assert restored.started_at == running_at
    assert restored.finished_at == finished_at


@pytest.mark.parametrize(
    ("operation", "scope"),
    [("produce", None), ("evolve", "workspace")],
)
def test_job_rejects_invalid_operation_scope_pair(operation, scope):
    with pytest.raises(ValueError):
        make_job(operation=operation, scope=scope)


def test_job_rejects_invalid_transitions_and_early_notification():
    queued = make_job()
    with pytest.raises(ValueError, match="queued -> succeeded"):
        queued.transition("succeeded")
    with pytest.raises(ValueError, match="terminal"):
        queued.with_notification_request("event")

    terminal = queued.transition("running").transition("failed", error="safe")
    with pytest.raises(ValueError, match="failed -> running"):
        terminal.transition("running")
    with pytest.raises(ValueError, match="no notification"):
        terminal.with_notification_status("not_found")
