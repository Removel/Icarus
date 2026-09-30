"""Transactional batch membership and delivery state."""
from dataclasses import asdict
from datetime import UTC, datetime
from uuid import uuid4
from sqlalchemy import select, update, text, and_, or_, exists
from sqlalchemy.orm import aliased
from .entities import _DecisionBatchRow, _ObservationRow
from ..entity.decision import ObservationBatch


class BatchStore:
    def __init__(self, observations):
        self.observations=observations

    async def close_batch(self, scope, member_ids, close_reason):
        if not member_ids or len(set(member_ids))!=len(member_ids): raise ValueError('Invalid batch members')
        async with self.observations._require_sessions().begin() as db:
            await db.execute(text('BEGIN IMMEDIATE'))
            rows=(await db.scalars(select(_ObservationRow).where(
                _ObservationRow.platform==scope[0],_ObservationRow.account_id==scope[1],
                _ObservationRow.subject==scope[2],_ObservationRow.batch_id.is_(None),
            ).order_by(_ObservationRow.ingest_seq).limit(len(member_ids)))).all()
            if tuple(r.id for r in rows)!=tuple(member_ids): raise ValueError('Batch must claim unassigned prefix')
            now=datetime.now(UTC); batch_id=uuid4().hex
            db.add(_DecisionBatchRow(id=batch_id,platform=scope[0],account_id=scope[1],subject=scope[2],
                opened_at=rows[0].received_at,closed_at=now,close_reason=close_reason,updated_at=now))
            for row in rows: row.batch_id=batch_id
        return await self.get(batch_id)

    async def get(self, id):
        async with self.observations._require_sessions()() as db:
            row=await db.get(_DecisionBatchRow,id)
            if row is None: raise KeyError(id)
            members=(await db.scalars(select(_ObservationRow).where(_ObservationRow.batch_id==id)
                                     .order_by(_ObservationRow.ingest_seq))).all()
            return ObservationBatch(row.id,row.platform,row.account_id,row.subject,
                tuple(self.observations._codec.from_row(m) for m in members),
                row.opened_at.replace(tzinfo=UTC),row.closed_at.replace(tzinfo=UTC))

    async def record(self,id):
        async with self.observations._require_sessions()() as db:
            row=await db.get(_DecisionBatchRow,id)
            if row is None: raise KeyError(id)
            return {c.name:getattr(row,c.name) for c in row.__table__.columns}

    async def transition(self,id,expected,**values):
        async with self.observations._require_sessions().begin() as db:
            result=await db.execute(update(_DecisionBatchRow).where(_DecisionBatchRow.id==id,
                _DecisionBatchRow.status.in_(expected)).values(**values,updated_at=datetime.now(UTC)))
            return bool(result.rowcount)

    async def claim_batch(self,id):
        return await self.transition(id,('ready',),status='judging')

    async def save_decision(self,id,run):
        status='failed' if run.status=='failed' else 'finished' if run.decision.action=='silence' else 'decided'
        return await self.transition(id,('judging',),status=status,route=asdict(run.decision),trace=run.trace(),graph_version='qq-graph-v1')

    async def begin_submission(self,id,method,params,operation_id, *, workspace_key=None):
        return await self.transition(id,('decided',),status='submitting',method=method,params=params,
            operation_id=operation_id,workspace_path=params.get('workspace_path'),
            workspace_key=workspace_key,session_id=params.get('session_id'))

    async def mark_receipt(self,id,response):
        return await self.transition(id,('submitting',),status='submitted',receipt=response)

    async def recover_batches(self, *, limit=100, exclude=()):
        if not 1 <= limit <= 1000:
            raise ValueError('Invalid recovery limit')
        row = _DecisionBatchRow
        older = aliased(row)
        earlier = or_(older.opened_at < row.opened_at,
                      and_(older.opened_at == row.opened_at, older.closed_at < row.closed_at),
                      and_(older.opened_at == row.opened_at, older.closed_at == row.closed_at, older.id < row.id))
        blocked = exists(select(older.id).where(
            older.platform == row.platform, older.account_id == row.account_id,
            older.subject == row.subject, earlier,
            older.status.in_(('ready', 'judging', 'decided', 'submitting', 'submission_unknown')),
        ))
        async with self.observations._require_sessions()() as db:
            return tuple((await db.scalars(select(row.id).where(
                row.status.in_(('ready', 'decided')), ~blocked, row.id.not_in(tuple(exclude)),
            ).order_by(row.opened_at, row.closed_at, row.id).limit(limit))).all())

    async def reset_interrupted(self):
        async with self.observations._require_sessions().begin() as db:
            await db.execute(update(_DecisionBatchRow).where(_DecisionBatchRow.status=='judging').values(status='ready'))
            await db.execute(update(_DecisionBatchRow).where(_DecisionBatchRow.status=='submitting').values(status='submission_unknown'))
