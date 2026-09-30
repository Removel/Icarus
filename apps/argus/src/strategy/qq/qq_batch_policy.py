"""Deterministic batch closure decisions, without owning timers."""
from dataclasses import dataclass
import unicodedata
from ..base_policy import BasePolicy
from ...entity.observation import Observation


@dataclass(frozen=True)
class BatchWindowInput:
    messages: tuple[Observation,...]
    opened_at: float
    last_human_at: float | None
    now: float
    quiet_seconds: float=5
    max_messages: int=20
    max_seconds: float=60
    bot_names: tuple[str,...]=()


@dataclass(frozen=True)
class BatchWindowResult:
    action: str
    deadline: float | None
    reason: str


def mentions_name(obs,names):
    value=unicodedata.normalize('NFKC',''.join(p.value for p in obs.content if p.kind=='text')).casefold()
    return any(unicodedata.normalize('NFKC',n).casefold() in value for n in names if n.strip())


class QQBatchPolicy(BasePolicy[BatchWindowInput,BatchWindowResult]):
    name,version='qq-batch','1'
    async def evaluate(self,value):
        if not value.messages: raise ValueError('Empty candidate')
        if len(value.messages)>=value.max_messages: return BatchWindowResult('close',None,'count')
        human=[m for m in value.messages if not getattr(m,'is_self',False)]
        if any(m.subject.kind=='private' or getattr(m,'mentions_bot',False) or mentions_name(m,value.bot_names) for m in human):
            return BatchWindowResult('close',None,'direct')
        deadline=value.opened_at+value.max_seconds
        if value.now>=deadline: return BatchWindowResult('close',None,'age')
        if value.last_human_at is not None:
            deadline=min(deadline,value.last_human_at+value.quiet_seconds)
        if value.now>=deadline: return BatchWindowResult('close',None,'quiet')
        return BatchWindowResult('wait',deadline,'collecting')
