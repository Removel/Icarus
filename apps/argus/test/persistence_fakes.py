"""Business fixture conveniences over the real row DAO and service functions."""
from datetime import UTC, datetime
from uuid import uuid4
from apps.argus.src.dao.database import Database
from apps.argus.src.dao.observation_store import ObservationStore as RowObservationStore
from apps.argus.src.dao.batch_store import BatchStore as RowBatchStore
from apps.argus.src.entity.qq_observation import QQObservation
from apps.argus.src.entity.session_link import SessionLink
from apps.argus.src.evidence.recent_activity import RecentActivityReader
from apps.argus.src.evidence.qq.qq_conversation import QQConversationReader as RowConversationReader
from apps.argus.src.evidence.icarus_state import IcarusStateReader as RowStateReader
from apps.argus.src.service.qq_service import QQService as RealQQService
from apps.argus.src.service.batch_scheduler import BatchScheduler as RealScheduler
from apps.argus.src.service.observation_status import ObservationStatusService
from apps.argus.src.service import batch_state
from apps.argus.src.strategy.batch_lifecycle import OPEN_STATES
from apps.argus.src.utils.datetime_utils import utc


class ObservationStore:
    def __init__(self, data_dir):
        self.database = Database(data_dir)
        self.rows = RowObservationStore(self.database)
        self.batches = RowBatchStore(self.database)
        self.database_path = self.database.database_path

    async def start(self):
        await self.database.start()

    async def close(self):
        await self.database.close()

    async def insert(self, obs, *, is_strong_signal=False):
        return await self.rows.insert(obs.to_row(is_strong_signal=is_strong_signal))

    async def get(self, id):
        return QQObservation.from_row(await self.rows.get(id))

    async def recent(self, *args, **kwargs):
        return tuple(QQObservation.from_row(row) for row in await self.rows.recent(*args, **kwargs))

    async def unassigned(self, *args, **kwargs):
        return tuple(QQObservation.from_row(row) for row in await self.rows.unassigned(*args, **kwargs))

    async def save_link(self, link):
        await self.rows.save_link(link.to_row(id=uuid4().hex, created_at=datetime.now(UTC)))

    async def current_link(self, *args):
        row = await self.rows.current_link(*args)
        return SessionLink.from_row(row) if row else None

    async def status_details(self, *args, **kwargs):
        return await ObservationStatusService(self.rows,self.batches).get(*args, **kwargs)

    async def conversation(self, batch):
        return await RowConversationReader(self.database,self.rows,self.batches).evaluate(batch=batch)

    async def preceding(self, batch, limit=10):
        row = await self.batches.get(batch.id)
        return tuple(QQObservation.from_row(r) for r in reversed(await self.rows.preceding_rows(
            (batch.platform,batch.account_id,batch.subject),row.first_ingest_seq,limit=limit)))

    def __getattr__(self, name):
        return getattr(self.rows, name)


class BatchStore:
    def __init__(self, store):
        self.store = store
        self.rows = store.batches

    async def close_batch(self, *args):
        return await batch_state.close_batch(self.store.database,self.store.rows,self.rows,*args)

    async def get(self, id):
        return await batch_state.load_batch(self.store.rows,self.rows,id)

    async def record(self, id):
        row = await self.rows.get(id)
        return {col.name: utc(value) if isinstance(value := getattr(row,col.name),datetime) else value
                for col in row.__table__.columns}

    async def transition(self, *args, **kwargs):
        return await self.rows.transition(*args, **kwargs)

    async def recent_activity(self, platform, account_id, subject, **kwargs):
        reader = RecentActivityReader(self.store.database,self.store.rows,self.rows,
                                      window_seconds=kwargs.pop('window_seconds',300))
        return await reader.read_scope((platform,account_id,subject), **kwargs)

    async def recover_batches(self, **kwargs):
        kwargs.setdefault('now', datetime.now(UTC))
        return await self.rows.recover_batches(open_states=OPEN_STATES,ready_states=('ready','decided'),**kwargs)

    async def waiting_heads(self, **kwargs):
        return await self.rows.waiting_heads(open_states=OPEN_STATES,**kwargs)

    async def is_head(self, id):
        return await self.rows.is_head(id,open_states=OPEN_STATES)

    async def postpone_check(self, id, **kwargs):
        return await self.rows.transition(id,('waiting',),**kwargs)

    def __getattr__(self, name):
        operation = getattr(batch_state,name)
        async def invoke(*args, **kwargs):
            return await operation(self.store.database,self.rows,*args,**kwargs)
        return invoke


class TypedGateway:
    """Use real client receipt/session rules around an external API test double."""
    def __init__(self, raw):
        self.raw = raw

    def __getattr__(self, name):
        return getattr(self.raw, name)

    async def ensure_session(self, path, sid):
        from apps.argus.src.clients.icarus_client import IcarusClient
        return await IcarusClient.ensure_session(self.raw,path,sid)

    async def _deliver(self, method, params):
        from apps.argus.src.clients.icarus_client import IcarusClient, GatewayBusinessError, GatewayTransportError
        from apps.argus.src.entity.api_result import DeliveryOutcome
        try:
            result = await getattr(self.raw,'session_steer' if method=='session.steer' else 'session_submit')(**params)
        except GatewayBusinessError as error:
            return DeliveryOutcome('failed' if error.code in {'session_not_found','submission_conflict','runtime_stopping','-32602'} else 'unknown',error_type=error.code)
        except (GatewayTransportError,TimeoutError):
            return DeliveryOutcome('unknown',error_type='transport')
        return IcarusClient.delivery_outcome(method,result,target_task_id=params.get('task_id'))

    async def session_submit(self, **params):
        return await self._deliver('session.submit',params)

    async def session_steer(self, **params):
        return await self._deliver('session.steer',params)


def QQService(store, judge, gateway, **kwargs):
    return RealQQService(store.database,store.rows,store.batches,judge,
                         TypedGateway(gateway) if gateway is not None else None,**kwargs)


class ScenarioScheduler(RealScheduler):
    async def offer(self, obs):
        self.notify((obs.source.platform,obs.source.account_id,obs.subject.key),human=not obs.is_self)
        await self.check_due()


def BatchScheduler(store, batches, **kwargs):
    return ScenarioScheduler(store.database,store.rows,store.batches,**kwargs)


def QQConversationReader(store):
    return RowConversationReader(store.database,store.rows,store.batches)


def IcarusStateReader(store, client, workspace_path, **kwargs):
    return RowStateReader(store.rows,store.batches,client,workspace_path,**kwargs)
