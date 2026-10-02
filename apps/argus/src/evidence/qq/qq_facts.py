"""Local batch facts and ordered decision evidence collection."""
from dataclasses import replace
from ...entity.evidence import QQBatchFacts, DecisionEvidence
from .qq_signals import mentions_name
from .qq_transcript import transcript
from ..icarus_state import work_signature


class QQBatchFactsReader:
    def __init__(self, names=()):
        self.names = names

    async def evaluate(self, batch):
        human = [m for m in batch.messages if not getattr(m,'is_self',False)]
        explicit = any((m.subject.kind=='private' or getattr(m,'mentions_bot',False))
            and any(p.kind in ('text','image') and p.value.strip() for p in m.content) for m in human)
        full = transcript(batch.messages)
        return QQBatchFacts(bool(human),explicit,dict(platform=batch.platform,subject=batch.subject,
            content=full[:20000],truncated=len(full)>20000,bot_names=list(self.names),
            is_direct=batch.messages[0].subject.kind=='private',
            name_mentioned=any(mentions_name(m,self.names) for m in human)))


class QQDecisionEvidenceReader:
    def __init__(self, conversation_reader, work_reader, activity_reader, facts_reader, batches, *, clock):
        self.conversation_reader, self.work_reader = conversation_reader, work_reader
        self.activity_reader, self.facts_reader = activity_reader, facts_reader
        self.batches, self.clock = batches, clock

    async def evaluate(self, *, batch):
        conversation = await self.conversation_reader.evaluate(batch=batch)
        snapshot = await self.work_reader.evaluate(batch=batch)
        activity = await self.activity_reader.evaluate(batch=batch,through_seq=conversation.through_seq,now=self.clock())
        facts = await self.facts_reader.evaluate(batch)
        before, following = transcript(conversation.preceding), transcript(conversation.following)
        state = dict(facts.state,recent_messages=before[:20000],recent_truncated=len(before)>20000,
            current_pending=facts.state['content'],following_background=following[:20000],
            has_conversation_context=bool(conversation.preceding or conversation.following or snapshot.history_records or snapshot.history_cursor),
            following_truncated=conversation.truncated or len(following)>20000,
            current_session=snapshot.session_id is not None,session_lifecycle=snapshot.lifecycle,
            active_task_count=len(snapshot.tasks),current_work=work_signature(snapshot),
            work_history=list(snapshot.history_records),
            recent_delivery=activity.last_delivery_at.isoformat() if activity.last_delivery_at else None,
            active_task_messages=[r['payload']['text'] for r in snapshot.history_records
                if r['type'] in {'user.message','user.correction'} and r['payload'].get('text')])
        try:
            record = await self.batches.get(batch.id)
        except KeyError:
            rechecking = False
        else:
            rechecking = bool(record.trace)
        return DecisionEvidence(conversation,snapshot,activity,replace(facts,state=state),
                                rechecking or state['has_conversation_context'])
