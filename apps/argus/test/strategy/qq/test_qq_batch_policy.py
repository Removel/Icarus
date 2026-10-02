from apps.argus.src.entity.policy_inputs import BatchTimingInput
import asyncio
from apps.argus.src.strategy.qq.qq_batch_policy import QQBatchPolicy
from apps.argus.src.entity.evidence import RecentActivity
from apps.argus.src.handler.qq_handler import QQHandler


def msg(n=1,kind='group',self_message=False):
    return QQHandler('10000').parse(dict(post_type='message',self_id=10000,time=1700000000,message_id=n,
        message_type=kind,group_id=42,user_id=10000 if self_message else 7,target_id=7,message='hello'))


def test_collection_and_explicit_signal_and_self_only():
    policy=QQBatchPolicy()
    kwargs=dict(messages=(msg(),),opened_at=0,last_human_at=2,now=2,human_pending_count=1,has_strong_signal=False,activity=RecentActivity())
    assert asyncio.run(policy.evaluate(BatchTimingInput(**kwargs))).action=='wait'
    kwargs['now']=7.99
    result = asyncio.run(policy.evaluate(BatchTimingInput(**kwargs)))
    assert result.action == 'wait' and result.deadline == 8
    kwargs['now']=8
    assert asyncio.run(policy.evaluate(BatchTimingInput(**kwargs))).action=='close'
    kwargs['has_strong_signal']=True
    assert asyncio.run(policy.evaluate(BatchTimingInput(**kwargs))).reason=='direct'
    kwargs['human_pending_count']=0
    assert asyncio.run(policy.evaluate(BatchTimingInput(**kwargs))).reason=='self_only'


def test_private_messages_wait_even_with_strong_signals_and_human_pressure():
    policy = QQBatchPolicy()
    messages = tuple(msg(n=n,kind='private') for n in range(1,7))
    value = BatchTimingInput(messages=messages,opened_at=0,last_human_at=4,now=9.99,
        human_pending_count=6,has_strong_signal=True,activity=RecentActivity())
    result = asyncio.run(policy.evaluate(value))
    assert result.action == 'wait' and result.deadline == 10
    from dataclasses import replace
    assert asyncio.run(policy.evaluate(replace(value,now=10))).action == 'close'
