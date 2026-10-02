"""Fixed-watermark conversation evidence, assembled outside persistence."""
from ...entity.evidence import ConversationEvidence
from ...entity.qq_observation import QQObservation


class QQConversationReader:
    def __init__(self, database, observations, batches):
        self.database, self.observations, self.batches = database, observations, batches

    async def evaluate(self, *, batch):
        async with self.database.begin() as session:
            try:
                row = await self.batches.get(batch.id, session=session)
            except KeyError:
                return ConversationEvidence(batch.messages, (), (), 0, 0, False)
            scope = (batch.platform,batch.account_id,batch.subject)
            through = await self.observations.message_watermark(*scope, session=session)
            human = await self.observations.message_watermark(*scope, human_only=True, session=session)
            before = await self.observations.preceding_rows(scope, row.first_ingest_seq, limit=11, session=session)
            count = await self.observations.count_following(scope, row.last_ingest_seq, through, session=session)
            if count > 20:
                early = await self.observations.following_rows(scope, row.last_ingest_seq, through, limit=10, session=session)
                late = await self.observations.following_rows(scope, row.last_ingest_seq, through,
                                                            limit=10, descending=True, session=session)
                after = (*early, *reversed(late))
            else:
                after = await self.observations.following_rows(scope, row.last_ingest_seq, through, limit=20, session=session)
            return ConversationEvidence(batch.messages,
                tuple(QQObservation.from_row(r) for r in reversed(before[:10])),
                tuple(QQObservation.from_row(r) for r in after), through, human, len(before)>10 or count>20)

    async def delivery_context(self, batch):
        async with self.database.begin() as session:
            try:
                row = await self.batches.get(batch.id, session=session)
            except KeyError:
                return ()
            records = await self.observations.delivery_context_rows((batch.platform,batch.account_id,batch.subject),
                                                                   row.first_ingest_seq, session=session)
            return tuple(QQObservation.from_row(r) for r, status in reversed(records) if status != 'submitted')
