"""Read-only material nodes; no Gateway write capabilities used."""
from datetime import UTC,datetime
from ..clients.icarus_client import GatewayBusinessError
from ..entity.decision import TaskSnapshot


class TaskStatusReader:
    def __init__(self,store,client,workspace_path):
        self.store=store;self.client=client;self.workspace_path=workspace_path
    async def evaluate(self,batch):
        link=await self.store.current_link(batch.platform,batch.account_id,batch.subject)
        if link is None or link.workspace_path!=self.workspace_path:
            return TaskSnapshot(self.workspace_path,None,None,(),'absent',datetime.now(UTC))
        try: status=await self.client.session_get(link.workspace_path,link.session_id)
        except GatewayBusinessError as error:
            if error.code!='session_not_found':raise
            return TaskSnapshot(self.workspace_path,None,None,(),'absent',datetime.now(UTC))
        if status['workspace_key']!=link.workspace_key:raise ValueError('Workspace identity mismatch')
        return TaskSnapshot(self.workspace_path,link.workspace_key,link.session_id,
            tuple(status.get('active_task_ids',())),status['lifecycle'],datetime.now(UTC))


class ContextReader:
    """Bounded prior messages plus ongoing-session facts for the judgement state."""
    def __init__(self, store):
        self.store = store

    async def evaluate(self, value):
        from dataclasses import replace
        from ..strategy.qq.qq_batch_facts_policy import transcript
        batch, facts, snapshot = value
        recent = await self.store.preceding(batch, limit=10)
        text = transcript(recent)
        state = dict(facts.state, recent_messages=text[:20000], recent_truncated=len(text) > 20000,
                     current_session=snapshot.session_id is not None,
                     session_lifecycle=snapshot.lifecycle,
                     active_task_count=len(snapshot.active_task_ids))
        return replace(facts, state=state)
