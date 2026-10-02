"""Interpret normal judgments and accepted delivery records as facts."""
from datetime import timedelta
from ..entity.evidence import RecentActivity
from ..utils.datetime_utils import utc


class RecentActivityReader:
    def __init__(self, database, observations, batches, *, window_seconds=300):
        self.database, self.observations, self.batches = database, observations, batches
        self.window_seconds = window_seconds

    async def evaluate(self, *, batch, through_seq, now):
        return await self.read_scope((batch.platform,batch.account_id,batch.subject),
                                     through_seq=through_seq, now=now, exclude=batch.id)

    async def read_scope(self, scope, *, through_seq, now, exclude=None):
        async with self.database.begin() as session:
            reset = await self.observations.latest_strong_signal(*scope, through_seq=through_seq, session=session)
            decisions = await self.batches.judged_rows(scope, through_seq=through_seq, exclude=exclude,
                statuses=('finished','submitted'), interventions=('yes','no'), session=session)
            count = 0
            for record in decisions:
                if (record.judged_through_seq or 0) < reset or record.intervention == 'yes':
                    break
                count += 1
            deliveries = await self.batches.delivered_rows(scope, through_seq=through_seq, exclude=exclude,
                since=now-timedelta(seconds=self.window_seconds), until=now, statuses=('submitted',), session=session)
            submits = [row for row in deliveries if row.method == 'session.submit']
            return RecentActivity(utc(decisions[0].judged_at) if decisions else None, count,
                utc(submits[0].delivered_at) if submits else None, len(submits),
                utc(deliveries[0].delivered_at) if deliveries else None, reset)
