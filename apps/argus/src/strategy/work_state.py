"""Judgments about verified work state and safe delivery eligibility."""
from .base_policy import BasePolicy
from ..entity.decision import RouteDecision
from ..entity.policy_inputs import DeliveryCheckInput


def running_tasks(snapshot):
    return tuple(t for t in snapshot.tasks if t.status_known and t.lifecycle == 'running')


def has_unknown_work(snapshot):
    return not snapshot.stable or any(not t.status_known for t in snapshot.tasks)


def has_queued_work(snapshot):
    return snapshot.queued_task_count > 0 or any(t.lifecycle == 'accepted' for t in snapshot.tasks)


def confirmed_idle(snapshot):
    return (snapshot.stable and snapshot.lifecycle in {'absent','ready','unloaded'}
            and not has_unknown_work(snapshot) and not has_queued_work(snapshot)
            and not running_tasks(snapshot)
            and not any((snapshot.pending_event_count,snapshot.pending_plugin_event_count,snapshot.background_work_count)))


class DeliveryCheckPolicy(BasePolicy[DeliveryCheckInput, RouteDecision]):
    name = 'delivery-check'

    async def evaluate(self, value):
        decision, snapshot = value.decision, value.snapshot
        reason = None
        if value.expected_fingerprint and value.expected_fingerprint != value.current_fingerprint:
            reason = 'evidence_changed'
        elif decision.action == 'submit' and not confirmed_idle(snapshot):
            reason = 'work_state_unresolved'
        elif decision.action == 'steer':
            running = running_tasks(snapshot)
            if (has_unknown_work(snapshot) or has_queued_work(snapshot) or len(running) != 1
                    or running[0].task_id != decision.target_task_id):
                reason = 'target_changed'
        if reason is None and value.final_status is not None:
            status = value.final_status
            if snapshot.lifecycle == 'absent':
                changed = (status['lifecycle'] not in {'ready','unloaded'} or bool(status.get('active_task_ids'))
                    or any(status.get(key,0) for key in ('queued_task_count','pending_event_count',
                                                        'pending_plugin_event_count','background_work_count')))
            else:
                changed = (value.expected_session_signature is None or value.final_session_signature is None
                           or value.expected_session_signature != value.final_session_signature)
            if changed or value.final_human_seq != value.human_seq:
                reason = 'evidence_changed'
        return RouteDecision('wait', reason) if reason else decision
