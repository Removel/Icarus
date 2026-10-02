"""Decide whether pending content belongs in the one verified running task."""
from typesafe_sdk import Choice
from ..base_policy import BasePolicy
from ...entity.decision import TaskRelationResult
from ...entity.policy_inputs import RelationInput
from ..work_state import running_tasks, has_unknown_work, has_queued_work, confirmed_idle


class QQTaskRelationPolicy(BasePolicy[RelationInput, TaskRelationResult]):
    name = 'qq-task-relation'

    def __init__(self, client):
        self.client = client

    async def evaluate(self, value):
        facts, snapshot = value.facts, value.snapshot
        if confirmed_idle(snapshot):
            return TaskRelationResult('undetermined', 'idle')
        running = running_tasks(snapshot)
        if has_unknown_work(snapshot) or has_queued_work(snapshot) or len(running) != 1:
            return TaskRelationResult('wait', 'work_state_unresolved')
        task_id = running[0].task_id
        requests = [r for r in snapshot.history_records if r.get('task_id') == task_id
                    and r['type'] in {'user.message','user.correction'} and r['payload'].get('text')]
        if not requests:
            return TaskRelationResult('wait', 'task_context_missing')
        response = await self.client.ask(state=facts.state, questions={'relation': Choice(
            instructions='本批待处理内容是否适合现在补充到当前执行中的任务？背景消息不是待执行请求。',
            criteria={'steer':'是当前工作的必要补充或纠正，适合现在追加。',
                      'wait':'仍需处理，但不适合打断当前工作，保留等待后重判。'})})
        return TaskRelationResult(response.answers['relation'].choice, 'jev')
