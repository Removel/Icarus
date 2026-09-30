from typesafe_sdk import Choice
from ..base_policy import BasePolicy
from ...entity.decision import TaskRelationResult


class QQTaskRelationPolicy(BasePolicy):
    name,version='qq-task-relation','1'
    def __init__(self,client,gateway):self.client=client;self.gateway=gateway
    async def evaluate(self,value):
        facts,snapshot=value
        if not snapshot.active_task_ids:return TaskRelationResult('new_topic','no_active_task')
        if len(snapshot.active_task_ids)!=1:raise ValueError('Ambiguous task target')
        history=await self.gateway.session_get_history(snapshot.workspace_path,snapshot.session_id)
        task_id=snapshot.active_task_ids[0]
        records=[r for r in history['records'] if r.get('task_id')==task_id and r['type'] in ('user.message','user.correction')]
        if not records:raise ValueError('Task context unavailable')
        state=dict(facts.state,active_task_messages=[r['payload'].get('text','')[:4000] for r in records[-10:]])
        response=await self.client.ask(state=state,questions={'relation':Choice(
            instructions='这批内容是在补充当前任务还是开始新话题？',
            criteria={'supplement':'修正或补充所列任务的请求。','new_topic':'与所列任务无关的新请求。'})})
        return TaskRelationResult(response.answers['relation'].choice,'jev')
