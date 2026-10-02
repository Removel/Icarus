"""Business routing uses verified work state, never topic labels."""
from ..base_policy import BasePolicy
from ...entity.decision import RouteDecision


from ..work_state import running_tasks, has_unknown_work, has_queued_work, confirmed_idle
from ...entity.policy_inputs import ActionInput

class QQActionPolicy(BasePolicy[ActionInput, RouteDecision]):
    name = 'qq-action'

    async def evaluate(self, value):
        relation, snapshot = value.relation, value.snapshot
        if has_unknown_work(snapshot):
            return RouteDecision('wait', 'work_state_unresolved')
        if has_queued_work(snapshot):
            return RouteDecision('wait', 'queued_work')
        running = running_tasks(snapshot)
        if len(running) == 1 and relation.verdict == 'steer':
            return RouteDecision('steer', 'supplement', running[0].task_id)
        if confirmed_idle(snapshot):
            return RouteDecision('submit', 'response_needed')
        return RouteDecision('wait', 'busy')
