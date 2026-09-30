"""Facts and bounded transcripts used by QQ decisions."""
from dataclasses import dataclass
from ..base_policy import BasePolicy
from .qq_batch_policy import mentions_name


def transcript(messages):
    return '\n'.join(
        ('[机器人] ' if getattr(m,'is_self',False) else '')
        + f'{m.actor.display_name or m.actor.external_id}: '
        + ''.join(p.value if p.kind=='text' else '[图片]' if p.kind=='image' else f'[{p.value}]'
                  for p in m.content)
        for m in messages)


@dataclass(frozen=True)
class QQBatchFacts:
    human_present: bool
    explicit_address: bool
    state: dict


class QQBatchFactsPolicy(BasePolicy):
    name,version='qq-batch-facts','1'
    def __init__(self,names=()):self.names=names
    async def evaluate(self,batch):
        human=[m for m in batch.messages if not getattr(m,'is_self',False)]
        explicit=any((m.subject.kind=='private' or getattr(m,'mentions_bot',False))
                     and any(p.kind in ('text','image') and p.value.strip() for p in m.content) for m in human)
        full=transcript(batch.messages)
        return QQBatchFacts(bool(human),explicit,dict(platform=batch.platform,subject=batch.subject,
            content=full[:20000],truncated=len(full)>20000,bot_names=list(self.names),
            is_direct=batch.messages[0].subject.kind=='private',
            name_mentioned=any(mentions_name(m,self.names) for m in human)))
