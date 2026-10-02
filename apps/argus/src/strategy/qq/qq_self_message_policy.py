"""Only-human participation guard."""
from ..base_policy import BasePolicy
from ...entity.decision import ObservationBatch


class QQSelfMessagePolicy(BasePolicy[ObservationBatch, bool]):
    name='qq-self-message'
    async def evaluate(self,value):
        batch = value
        return any(not getattr(m,'is_self',False) for m in batch.messages)
