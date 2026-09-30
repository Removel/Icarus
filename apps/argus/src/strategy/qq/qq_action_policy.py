from ..base_policy import BasePolicy
from ...entity.decision import RouteDecision


class QQActionPolicy(BasePolicy):
    name,version='qq-action','1'
    async def evaluate(self,value):
        relation,snapshot=value
        if relation.verdict=='new_topic':return RouteDecision('submit',relation.reason)
        if relation.verdict=='supplement' and len(snapshot.active_task_ids)==1:
            return RouteDecision('steer',relation.reason,snapshot.active_task_ids[0])
        raise ValueError('Insufficient evidence for a route')
