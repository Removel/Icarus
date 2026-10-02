"""Observation and session-link table access, without business mapping."""
from datetime import UTC, datetime
from sqlalchemy import select, update, func
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from ..entity.records import ObservationRow, SessionLinkRow, DecisionBatchRow
from ..utils.datetime_utils import utc


from ..entity.observation import ObservationConflictError


class ObservationStore:
    def __init__(self, database):
        self.database = database

    @staticmethod
    def _scope(scope):
        return (ObservationRow.platform == scope[0], ObservationRow.account_id == scope[1],
                ObservationRow.subject == scope[2])

    async def insert(self, row: ObservationRow, *, session=None):
        if not isinstance(row, ObservationRow):
            raise TypeError('Expected ObservationRow')
        async with self.database.use(session, write=True) as db:
            fields = {col.name: getattr(row, col.name) for col in row.__table__.columns}
            fields['ingest_seq'] = (await db.scalar(select(func.max(ObservationRow.ingest_seq))) or 0) + 1
            result = await db.execute(sqlite_insert(ObservationRow).values(**fields).on_conflict_do_nothing(
                index_elements=('platform', 'account_id', 'source_message_id')))
            if result.rowcount:
                return True
            existing = await db.scalar(select(ObservationRow).where(ObservationRow.platform == row.platform,
                ObservationRow.account_id == row.account_id, ObservationRow.source_message_id == row.source_message_id))
            facts = ('platform','account_id','source_message_id','subject','sender_id','sender_name',
                     'occurred_at','content','subtype','detail_version','detail')
            if any((utc(getattr(existing, key)) != utc(getattr(row, key)) if key == 'occurred_at'
                    else getattr(existing, key) != getattr(row, key)) for key in facts):
                raise ObservationConflictError('Conflicting observation for the same source message')
            return False

    async def get(self, id, *, session=None):
        async with self.database.use(session) as db:
            row = await db.get(ObservationRow, id)
            if row is None:
                raise KeyError(id)
            return row

    async def recent(self, platform, account_id, subject, limit=20, *, session=None):
        if not 1 <= limit <= 100:
            raise ValueError('limit must be between 1 and 100')
        async with self.database.use(session) as db:
            return tuple((await db.scalars(select(ObservationRow).where(*self._scope((platform,account_id,subject)))
                .order_by(ObservationRow.received_at.desc(), ObservationRow.id.desc()).limit(limit))).all())

    async def member_rows(self, batch_id, *, session=None):
        async with self.database.use(session) as db:
            return tuple((await db.scalars(select(ObservationRow).where(ObservationRow.batch_id == batch_id)
                .order_by(ObservationRow.ingest_seq))).all())

    async def latest_strong_signal(self, platform, account_id, subject, *, through_seq=None, session=None):
        conditions = [*self._scope((platform,account_id,subject)), ObservationRow.is_strong_signal.is_(True)]
        if through_seq is not None:
            conditions.append(ObservationRow.ingest_seq <= through_seq)
        async with self.database.use(session) as db:
            return (await db.scalar(select(func.max(ObservationRow.ingest_seq)).where(*conditions))) or 0

    async def message_watermark(self, platform, account_id, subject, *, human_only=False, session=None):
        conditions = list(self._scope((platform,account_id,subject)))
        if human_only:
            conditions.append(ObservationRow.detail['is_self'].as_boolean().is_(False))
        async with self.database.use(session) as db:
            return (await db.scalar(select(func.max(ObservationRow.ingest_seq)).where(*conditions))) or 0

    async def preceding_rows(self, scope, first_seq, *, limit, session=None):
        async with self.database.use(session) as db:
            return tuple((await db.scalars(select(ObservationRow).where(*self._scope(scope),
                ObservationRow.ingest_seq < first_seq).order_by(ObservationRow.ingest_seq.desc()).limit(limit))).all())

    async def following_rows(self, scope, last_seq, through_seq, *, limit, descending=False, session=None):
        order = ObservationRow.ingest_seq.desc() if descending else ObservationRow.ingest_seq
        async with self.database.use(session) as db:
            return tuple((await db.scalars(select(ObservationRow).where(*self._scope(scope),
                ObservationRow.ingest_seq > last_seq, ObservationRow.ingest_seq <= through_seq)
                .order_by(order).limit(limit))).all())

    async def count_following(self, scope, last_seq, through_seq, *, session=None):
        async with self.database.use(session) as db:
            return await db.scalar(select(func.count()).select_from(ObservationRow).where(*self._scope(scope),
                ObservationRow.ingest_seq > last_seq, ObservationRow.ingest_seq <= through_seq))

    async def delivery_context_rows(self, scope, first_seq, *, limit=10, session=None):
        async with self.database.use(session) as db:
            return tuple((await db.execute(select(ObservationRow, DecisionBatchRow.status)
                .outerjoin(DecisionBatchRow, ObservationRow.batch_id == DecisionBatchRow.id)
                .where(*self._scope(scope), ObservationRow.ingest_seq < first_seq)
                .order_by(ObservationRow.ingest_seq.desc()).limit(limit))).all())

    async def unassigned(self, scope, limit=20, *, session=None):
        async with self.database.use(session) as db:
            return tuple((await db.scalars(select(ObservationRow).where(*self._scope(scope),
                ObservationRow.batch_id.is_(None)).order_by(ObservationRow.ingest_seq).limit(limit))).all())

    async def pending_summary(self, scope, *, session=None):
        conditions = (*self._scope(scope), ObservationRow.batch_id.is_(None),
                      ObservationRow.detail['is_self'].as_boolean().is_(False))
        async with self.database.use(session) as db:
            count = await db.scalar(select(func.count()).select_from(ObservationRow).where(*conditions))
            strong = await db.scalar(select(func.count()).select_from(ObservationRow).where(*conditions,
                ObservationRow.is_strong_signal.is_(True)))
            return count, bool(strong)

    async def unassigned_scopes(self, *, session=None):
        async with self.database.use(session) as db:
            return tuple((await db.execute(select(ObservationRow.platform, ObservationRow.account_id,
                ObservationRow.subject).where(ObservationRow.batch_id.is_(None)).distinct())).all())

    async def current_link(self, platform, account_id, subject, *, session=None):
        async with self.database.use(session) as db:
            return await db.scalar(select(SessionLinkRow).where(SessionLinkRow.platform == platform,
                SessionLinkRow.account_id == account_id, SessionLinkRow.subject == subject,
                SessionLinkRow.ended_at.is_(None)))

    async def save_link(self, row: SessionLinkRow, *, session=None):
        async with self.database.use(session, write=True) as db:
            await db.execute(update(SessionLinkRow).where(SessionLinkRow.platform == row.platform,
                SessionLinkRow.account_id == row.account_id, SessionLinkRow.subject == row.subject,
                SessionLinkRow.ended_at.is_(None)).values(ended_at=datetime.now(UTC)))
            db.add(row)
