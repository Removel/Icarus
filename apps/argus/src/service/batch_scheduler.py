"""Bounded batch scheduling; model execution is a separate consumer."""
import asyncio
from datetime import UTC,datetime
from ..strategy.qq.qq_batch_policy import QQBatchPolicy,BatchWindowInput


class BatchScheduler:
    def __init__(self, observations,batches, *, quiet_seconds=5,max_messages=20,max_seconds=60,
                 bot_names=(),capacity=100,clock=lambda:datetime.now(UTC).timestamp()):
        self.observations=observations;self.batches=batches
        self.policy=QQBatchPolicy();self.quiet_seconds=quiet_seconds
        self.max_messages=max_messages;self.max_seconds=max_seconds;self.bot_names=bot_names
        self.clock=clock;self.ready=asyncio.Queue(maxsize=capacity);self.scheduled=set()
        self._lock=asyncio.Lock()

    async def offer(self,obs):
        await self._check_scope((obs.source.platform,obs.source.account_id,obs.subject.key),self.clock())
        await self.replenish()

    async def _check_scope(self,scope,now):
        async with self._lock:
            messages=await self.observations.unassigned(scope,self.max_messages)
            if not messages:return
            human=[m.received_at.timestamp() for m in messages if not getattr(m,'is_self',False)]
            decision=await self.policy.evaluate(BatchWindowInput(messages,messages[0].received_at.timestamp(),
                max(human) if human else None,now,self.quiet_seconds,self.max_messages,self.max_seconds,self.bot_names))
            if decision.action=='close':
                await self.batches.close_batch(scope,tuple(m.id for m in messages),decision.reason)

    async def check_due(self,now=None):
        for scope in await self.observations.unassigned_scopes():
            await self._check_scope(scope,self.clock() if now is None else now)
        await self.replenish()

    async def replenish(self):
        capacity = self.ready.maxsize - self.ready.qsize()
        if capacity <= 0:
            return
        for batch_id in await self.batches.recover_batches(limit=min(capacity, 1000), exclude=self.scheduled):
            if batch_id in self.scheduled:continue
            try:self.ready.put_nowait(batch_id)
            except asyncio.QueueFull:break
            self.scheduled.add(batch_id)

    def done(self,batch_id):
        self.scheduled.discard(batch_id)
        self.ready.task_done()
