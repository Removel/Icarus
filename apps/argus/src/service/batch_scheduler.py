"""Collection and bounded wakeups; model execution is a separate consumer."""
import asyncio
import logging
from datetime import UTC, datetime
from ..strategy.qq.qq_batch_policy import QQBatchPolicy
from ..strategy.batch_lifecycle import OPEN_STATES
from ..entity.qq_observation import QQObservation
from ..entity.policy_inputs import BatchTimingInput
from ..utils.datetime_utils import utc
from ..evidence.recent_activity import RecentActivityReader
from .batch_state import close_batch

logger = logging.getLogger('argus.scheduler')


class BatchScheduler:
    def __init__(self, database, observations, batches, *, quiet_seconds=6, max_messages=20, max_seconds=60,
                 bot_names=(), capacity=100, clock=lambda:datetime.now(UTC).timestamp(),
                 waiting_changed=None, base_seconds=15, start_count=2, cap_seconds=300,
                 spacing_seconds=15, bypass_messages=6, submission_window_seconds=300):
        self.database, self.observations, self.batches = database, observations, batches
        self.activity_reader = RecentActivityReader(database,observations,batches,window_seconds=submission_window_seconds)
        self.policy = QQBatchPolicy(quiet_seconds=quiet_seconds,max_messages=max_messages,max_seconds=max_seconds,
            base_seconds=base_seconds,start_count=start_count,cap_seconds=cap_seconds,
            spacing_seconds=spacing_seconds,bypass_messages=bypass_messages)
        self.max_messages, self.bot_names = max_messages, bot_names
        self.clock, self.waiting_changed = clock, waiting_changed
        self.submission_window_seconds = submission_window_seconds
        self.ready = asyncio.Queue(maxsize=capacity)
        self.scheduled = set()
        self._lock = asyncio.Lock()
        self.wakeup = asyncio.Event()
        self._human_scopes = set()

    def notify(self, scope, *, human):
        if human:
            self._human_scopes.add(scope)
        self.wakeup.set()

    async def _check_scope(self, scope, now):
        async with self._lock:
            messages = tuple(QQObservation.from_row(row) for row in await self.observations.unassigned(scope,self.max_messages))
            if not messages:
                return
            human = [m.received_at.timestamp() for m in messages if not m.is_self]
            count,strong = await self.observations.pending_summary(scope)
            through = await self.observations.message_watermark(*scope)
            activity = await self.activity_reader.read_scope(scope,through_seq=through,now=datetime.fromtimestamp(now,UTC))
            # Drain self-only prefixes without consulting a model, even if a human follows further back.
            decision = await self.policy.evaluate(BatchTimingInput(messages,messages[0].received_at.timestamp(),
                max(human) if human else None,now,count if human else 0,
                (strong or messages[0].subject.kind == 'private') if human else False,activity))
            if decision.action == 'close':
                await close_batch(self.database,self.observations,self.batches,scope,tuple(m.id for m in messages),decision.reason)

    async def _enqueue(self, id):
        if id in self.scheduled:
            return
        try:
            self.ready.put_nowait(id)
        except asyncio.QueueFull:
            return
        self.scheduled.add(id)

    async def _check_waiting(self, now, *, scope=None, force=False):
        if self.waiting_changed is None:
            return
        for id in await self.batches.waiting_heads(open_states=OPEN_STATES):
            if id in self.scheduled or self.ready.full():
                continue
            record = await self.batches.get(id)
            if scope is not None and (record.platform,record.account_id,record.subject) != scope:
                continue
            if not force and record.next_check_at and utc(record.next_check_at).timestamp() > now:
                continue
            try:
                changed = await self.waiting_changed(id,now=datetime.fromtimestamp(now,UTC))
            except Exception as error:
                logger.warning('waiting check failed batch=%s error=%s',id,type(error).__name__)
                from datetime import timedelta
                await self.batches.transition(id,('waiting',),next_check_at=datetime.fromtimestamp(now,UTC)+timedelta(seconds=5))
                continue
            if changed:
                await self._enqueue(id)

    async def check_due(self, now=None):
        now = self.clock() if now is None else now
        human_scopes, self._human_scopes = self._human_scopes, set()
        for scope in await self.observations.unassigned_scopes():
            await self._check_scope(scope,now)
        for scope in human_scopes:
            await self._check_waiting(now,scope=scope,force=True)
        await self._check_waiting(now)
        await self.replenish()

    async def replenish(self):
        capacity = self.ready.maxsize-self.ready.qsize()
        if capacity <= 0:
            return
        for id in await self.batches.recover_batches(open_states=OPEN_STATES,ready_states=('ready','decided'),limit=min(capacity,1000),exclude=self.scheduled,now=datetime.fromtimestamp(self.clock(),UTC)):
            await self._enqueue(id)

    def done(self, id):
        self.scheduled.discard(id)
        self.ready.task_done()
