"""Only-human participation guard."""
from ..base_policy import BasePolicy


class QQSelfMessagePolicy(BasePolicy):
    name,version='qq-self-message','1'
    async def evaluate(self,batch):
        return any(not getattr(m,'is_self',False) for m in batch.messages)
