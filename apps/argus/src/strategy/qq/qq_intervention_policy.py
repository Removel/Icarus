from ..base_policy import BasePolicy
from ...entity.decision import InterventionResult


class QQInterventionPolicy(BasePolicy):
    name,version='qq-intervention','1'
    async def evaluate(self,facts):
        if not facts.human_present:return InterventionResult('no','self_only')
        if facts.explicit_address:return InterventionResult('yes','explicit_address')
        return InterventionResult('undetermined','semantic_context_required')
