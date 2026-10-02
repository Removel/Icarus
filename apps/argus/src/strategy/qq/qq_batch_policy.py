"""Pure collection and pacing rules; timers belong to the scheduler."""
from math import ceil, log2
from ...entity.decision import BatchWindowResult
from ...entity.policy_inputs import BatchTimingInput
from ..base_policy import BasePolicy


class QQBatchPolicy(BasePolicy[BatchTimingInput, BatchWindowResult]):
    name = 'qq-batch'
    def __init__(self, *, quiet_seconds=6, max_messages=20, max_seconds=60,
                 base_seconds=15, start_count=2, cap_seconds=300,
                 spacing_seconds=15, bypass_messages=6):
        self.quiet_seconds = quiet_seconds
        self.max_messages = max_messages
        self.max_seconds = max_seconds
        self.base_seconds = base_seconds
        self.start_count = start_count
        self.cap_seconds = cap_seconds
        self.spacing_seconds = spacing_seconds
        self.bypass_messages = bypass_messages

    async def evaluate(self, value):
        messages, opened_at, last_human_at, now = value.messages, value.opened_at, value.last_human_at, value.now
        human_pending_count, has_strong_signal, activity = value.human_pending_count, value.has_strong_signal, value.activity
        if not messages:
            raise ValueError('Empty candidate')
        if not human_pending_count:
            return BatchWindowResult('close', None, 'self_only')
        collect = min(last_human_at + self.quiet_seconds, opened_at + self.max_seconds)
        if len(messages) >= self.max_messages:
            collect = now
        if messages[0].subject.kind == 'private':
            return BatchWindowResult('close' if now >= collect else 'wait', collect, 'private_collection')
        if has_strong_signal:
            return BatchWindowResult('close', None, 'direct')
        if human_pending_count >= self.bypass_messages:
            return BatchWindowResult('close', None, 'pressure')
        deadline = collect
        if activity.last_judged_at and activity.consecutive_no_action >= self.start_count:
            bound = max(0, ceil(log2(self.cap_seconds / self.base_seconds)))
            exponent = min(bound, activity.consecutive_no_action - self.start_count)
            delay = min(self.cap_seconds, self.base_seconds * 2 ** exponent)
            deadline = max(deadline, activity.last_judged_at.timestamp() + delay)
        if activity.last_submit_at:
            delay = min(self.cap_seconds, self.spacing_seconds * activity.submissions_in_window)
            deadline = max(deadline, activity.last_submit_at.timestamp() + delay)
        return BatchWindowResult('close' if now >= deadline else 'wait', deadline, 'pacing')
