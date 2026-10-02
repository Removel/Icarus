"""Safe observation status views composed from table records."""
from ..utils.datetime_utils import utc


class ObservationStatusService:
    def __init__(self, observations, batches):
        self.observations, self.batches = observations, batches

    async def get(self, id, *, after_run=0, limit=20):
        if after_run < 0 or not 1 <= limit <= 100:
            raise ValueError('Invalid run pagination')
        async with self.observations.database.begin() as session:
            row = await self.observations.get(id, session=session)
            if row.batch_id is None:
                return dict(observation_id=id,batch_id=None,status='pending',runs=[],
                            next_after_run=after_run,has_more=False)
            batch = await self.batches.get(row.batch_id, session=session)
            traces = batch.trace or []
            runs = traces[after_run:after_run+limit]
            next_after = after_run+len(runs)
            return dict(observation_id=id,batch_id=row.batch_id,status=batch.status,
                session_id=batch.session_id,submission_id=batch.operation_id,
                task_id=(batch.receipt or {}).get('task_id'),error_type=batch.error_type,
                route=batch.route,next_check_at=utc(batch.next_check_at),
                wait_reason=(batch.route or {}).get('reason') if batch.status=='waiting' else None,
                runs=runs,next_after_run=next_after,has_more=next_after<len(traces))
