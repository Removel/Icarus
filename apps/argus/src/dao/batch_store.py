"""Batch table queries and conditional atomic persistence."""
from datetime import UTC, datetime
from sqlalchemy import select, update, exists, or_
from sqlalchemy.orm import aliased
from ..entity.records import DecisionBatchRow, ObservationRow


class BatchStore:
    def __init__(self, database):
        self.database = database

    async def create(self, row: DecisionBatchRow, member_ids, *, session=None):
        if not member_ids or len(set(member_ids)) != len(member_ids):
            raise ValueError('Invalid batch members')
        async with self.database.use(session, write=True) as db:
            members = tuple((await db.scalars(select(ObservationRow).where(
                ObservationRow.platform == row.platform, ObservationRow.account_id == row.account_id,
                ObservationRow.subject == row.subject, ObservationRow.batch_id.is_(None))
                .order_by(ObservationRow.ingest_seq).limit(len(member_ids)))).all())
            if (tuple(m.id for m in members) != tuple(member_ids)
                    or members[0].ingest_seq != row.first_ingest_seq
                    or members[-1].ingest_seq != row.last_ingest_seq):
                raise ValueError('Batch must claim unassigned prefix')
            db.add(row)
            for member in members:
                member.batch_id = row.id

    async def get(self, id, *, session=None):
        async with self.database.use(session) as db:
            row = await db.get(DecisionBatchRow, id)
            if row is None:
                raise KeyError(id)
            return row

    async def transition(self, id, expected, *, session=None, **values):
        async with self.database.use(session, write=True) as db:
            result = await db.execute(update(DecisionBatchRow).where(DecisionBatchRow.id == id,
                DecisionBatchRow.status.in_(expected)).values(**values, updated_at=datetime.now(UTC)))
            return bool(result.rowcount)

    @staticmethod
    def _unblocked(open_states):
        row, older = DecisionBatchRow, aliased(DecisionBatchRow)
        return ~exists(select(older.id).where(older.platform == row.platform,
            older.account_id == row.account_id, older.subject == row.subject,
            older.first_ingest_seq < row.first_ingest_seq, older.status.in_(open_states)))

    async def recover_batches(self, *, now, open_states, ready_states, limit=100, exclude=(), session=None):
        if not 1 <= limit <= 1000:
            raise ValueError('Invalid recovery limit')
        row = DecisionBatchRow
        async with self.database.use(session) as db:
            return tuple((await db.scalars(select(row.id).where(row.status.in_(ready_states),
                self._unblocked(open_states), row.id.not_in(tuple(exclude)),
                or_(row.next_check_at.is_(None), row.next_check_at <= now))
                .order_by(row.first_ingest_seq).limit(limit))).all())

    async def waiting_heads(self, *, open_states, waiting_status='waiting', limit=100, session=None):
        row = DecisionBatchRow
        async with self.database.use(session) as db:
            return tuple((await db.scalars(select(row.id).where(row.status == waiting_status,
                self._unblocked(open_states)).order_by(row.next_check_at, row.first_ingest_seq).limit(limit))).all())

    async def is_head(self, id, *, open_states, session=None):
        async with self.database.use(session) as db:
            return bool(await db.scalar(select(DecisionBatchRow.id).where(DecisionBatchRow.id == id,
                self._unblocked(open_states))))

    async def rows_in_states(self, states, *, session=None):
        async with self.database.use(session) as db:
            return tuple((await db.scalars(select(DecisionBatchRow).where(DecisionBatchRow.status.in_(states)))).all())

    @staticmethod
    def _scope(scope):
        row = DecisionBatchRow
        return (row.platform == scope[0], row.account_id == scope[1], row.subject == scope[2])

    async def judged_rows(self, scope, *, through_seq, statuses, interventions, exclude=None, session=None):
        row = DecisionBatchRow
        async with self.database.use(session) as db:
            return tuple((await db.scalars(select(row).where(*self._scope(scope), row.id != exclude,
                row.last_ingest_seq <= through_seq, row.judged_at.is_not(None),
                row.intervention.in_(interventions), row.status.in_(statuses))
                .order_by(row.judged_at.desc(), row.first_ingest_seq.desc()))).all())

    async def delivered_rows(self, scope, *, through_seq, since, until, statuses, exclude=None, session=None):
        row = DecisionBatchRow
        async with self.database.use(session) as db:
            return tuple((await db.scalars(select(row).where(*self._scope(scope), row.id != exclude,
                row.delivered_at.is_not(None), row.status.in_(statuses), row.last_ingest_seq <= through_seq,
                row.delivered_at >= since, row.delivered_at <= until).order_by(row.delivered_at.desc()))).all())

    async def task_delivery_rows(self, scope, *, task_id, limit=100, session=None):
        row = DecisionBatchRow
        async with self.database.use(session) as db:
            records = (await db.scalars(select(row).where(*self._scope(scope),
                row.status == 'submitted', row.method == 'session.submit')
                .order_by(row.first_ingest_seq.desc()).limit(limit))).all()
            return tuple(record for record in records if (record.receipt or {}).get('task_id') == task_id)
