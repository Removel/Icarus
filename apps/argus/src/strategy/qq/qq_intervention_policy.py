from ..base_policy import BasePolicy
from ...entity.decision import InterventionResult
from ..work_state import confirmed_idle
from ...entity.policy_inputs import InterventionInput


class QQInterventionPolicy(BasePolicy[InterventionInput, InterventionResult]):
    name = 'qq-intervention'

    async def evaluate(self, value):
        facts, snapshot, activity, rechecking = value.facts, value.snapshot, value.activity, value.rechecking
        if not facts.human_present:
            return InterventionResult('no','self_only')
        if facts.explicit_address and confirmed_idle(snapshot) and activity.last_delivery_at is None and not rechecking:
            return InterventionResult('yes','explicit_address')
        return InterventionResult('undetermined','semantic_context_required')
