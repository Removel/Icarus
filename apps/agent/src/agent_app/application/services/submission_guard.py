"""Submission guard application behavior."""

from __future__ import annotations


from icarus.core.contracts.input import InputAccepted
from icarus.core.run_control import TaskOperationResult
from agent_app.application.models.status import TaskStatus


from agent_app.application.models.session_entry import _SessionEntry, _SubmissionRecord, _SteerSubmissionRecord


class SubmissionGuard:
    def __init__(self, *, submission_history_limit, task_history_limit):
        self.submission_history_limit = submission_history_limit
        self.task_history_limit = task_history_limit

    def _remember_submission(
        self,
        entry: _SessionEntry,
        submission_id: str,
        fingerprint: str,
        accepted: InputAccepted,
    ) -> None:
        entry.submissions[submission_id] = _SubmissionRecord(
            fingerprint, accepted
        )
        entry.submissions.move_to_end(submission_id)
        while len(entry.submissions) > self.submission_history_limit:
            entry.submissions.popitem(last=False)

    def _remember_steer_submission(
        self,
        entry: _SessionEntry,
        submission_id: str,
        fingerprint: str,
        result: TaskOperationResult,
    ) -> None:
        entry.steer_submissions[submission_id] = _SteerSubmissionRecord(
            fingerprint, result
        )
        entry.steer_submissions.move_to_end(submission_id)
        while len(entry.steer_submissions) > self.submission_history_limit:
            entry.steer_submissions.popitem(last=False)

    def _remember_task(self, entry: _SessionEntry, status: TaskStatus) -> None:
        entry.tasks[status.task_id] = status
        entry.tasks.move_to_end(status.task_id)
        while len(entry.tasks) > self.task_history_limit:
            entry.tasks.popitem(last=False)

