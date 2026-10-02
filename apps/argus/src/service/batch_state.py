"""Compose atomic batch writes without putting business decisions in DAO."""
from dataclasses import asdict
from datetime import UTC, datetime
import hashlib
import json
from uuid import uuid4
from ..entity.decision import ObservationBatch
from ..strategy.batch_lifecycle import status_for_decision, recovery_status


async def load_batch(observations, batches, id):
    async with batches.database.begin() as session:
        row = await batches.get(id, session=session)
        members = await observations.member_rows(id, session=session)
        return ObservationBatch.from_row(row, members)


async def close_batch(database, observations, batches, scope, member_ids, close_reason, *, now=None):
    async with database.begin(immediate=True) as session:
        rows = await observations.unassigned(scope, len(member_ids), session=session)
        if not rows or tuple(row.id for row in rows) != tuple(member_ids):
            raise ValueError('Batch must claim unassigned prefix')
        now = now or datetime.now(UTC)
        from ..entity.qq_observation import QQObservation
        from ..utils.datetime_utils import utc
        batch = ObservationBatch(uuid4().hex, *scope, tuple(QQObservation.from_row(row) for row in rows),
                                 utc(rows[0].received_at), now)
        await batches.create(batch.to_row(first_ingest_seq=rows[0].ingest_seq,
            last_ingest_seq=rows[-1].ingest_seq, close_reason=close_reason, status='ready'), member_ids, session=session)
        return batch


async def claim_batch(database, batches, id):
    async with database.begin(immediate=True) as session:
        row = await batches.get(id, session=session)
        if row.status not in ('ready', 'waiting'):
            return False
        return await batches.transition(id, (row.status,), session=session, resume_status=row.status, status='judging')


async def save_decision(database, batches, id, *, decision, run_status, trace, judged_at=None,
                        intervention=None, judged_through_seq=None, next_check_at=None,
                        wait_fingerprint=None, decision_message_seq=None):
    async with database.begin(immediate=True) as session:
        row = await batches.get(id, session=session)
        return await batches.transition(id, ('judging',), session=session,
            status=status_for_decision(decision.action, run_status), route=asdict(decision),
            trace=[*(row.trace or []), trace], judged_at=judged_at,
            intervention=intervention if run_status == 'completed' else None,
            judged_through_seq=judged_through_seq, next_check_at=next_check_at,
            wait_fingerprint=wait_fingerprint, decision_message_seq=decision_message_seq)


async def defer(database, batches, id, *, reason, next_check_at, fingerprint, trace=None):
    async with database.begin(immediate=True) as session:
        row = await batches.get(id, session=session)
        return await batches.transition(id, ('decided','judging','submitting','waiting'), session=session,
            status='waiting', route=dict(action='wait',reason=reason,target_task_id=None),
            next_check_at=next_check_at, wait_fingerprint=fingerprint,
            trace=[*(row.trace or []), trace] if trace is not None else row.trace)


async def begin_submission(database, batches, id, method, params, operation_id, *, workspace_key=None):
    async with database.begin(immediate=True) as session:
        row = await batches.get(id, session=session)
        attempts = [*(row.delivery_attempts or []), dict(operation_id=operation_id, method=method,
            task_id=params.get('task_id'), payload_hash=hashlib.sha256(json.dumps(params,sort_keys=True).encode()).hexdigest(),
            outcome='unknown')]
        return await batches.transition(id, ('decided',), session=session, status='submitting',
            delivery_attempts=attempts, method=method, params=params, operation_id=operation_id,
            workspace_path=params.get('workspace_path'), workspace_key=workspace_key, session_id=params.get('session_id'))


async def mark_receipt(database, batches, id, response, *, delivered_at=None):
    async with database.begin(immediate=True) as session:
        row = await batches.get(id, session=session)
        attempts = list(row.delivery_attempts or [])
        if attempts:
            attempts[-1] = dict(attempts[-1], outcome='accepted')
        return await batches.transition(id, ('submitting',), session=session, status='submitted', receipt=response,
            delivered_at=delivered_at or datetime.now(UTC), delivery_attempts=attempts)


async def mark_rejected(database, batches, id, response, *, next_check_at):
    async with database.begin(immediate=True) as session:
        row = await batches.get(id, session=session)
        attempts = list(row.delivery_attempts or [])
        if attempts:
            attempts[-1] = dict(attempts[-1], outcome='rejected')
        return await batches.transition(id, ('submitting',), session=session, status='waiting', receipt=response,
            route=dict(action='wait',reason='steer_rejected',target_task_id=None),
            next_check_at=next_check_at, delivery_attempts=attempts)


async def reset_interrupted(database, batches):
    async with database.begin(immediate=True) as session:
        for row in await batches.rows_in_states(('judging','submitting'), session=session):
            await batches.transition(row.id, (row.status,), session=session,
                                     status=recovery_status(row.status, row.resume_status))
