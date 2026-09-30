import asyncio
from dataclasses import replace
from apps.argus.src.strategy.qq.qq_batch_policy import QQBatchPolicy, BatchWindowInput
from apps.argus.src.handler.qq_handler import QQHandler


def msg(n=1,kind='group',self_message=False):
    return QQHandler('10000').parse(dict(post_type='message',self_id=10000,time=1700000000,message_id=n,
        message_type=kind,group_id=42,user_id=10000 if self_message else 7,target_id=7,message='hello'))


def test_quiet_and_max_limits_and_private_immediate():
    policy=QQBatchPolicy()
    value=BatchWindowInput((msg(),),0,2,6)
    assert asyncio.run(policy.evaluate(value)).action=='wait'
    assert asyncio.run(policy.evaluate(replace(value,now=7))).reason=='quiet'
    assert asyncio.run(policy.evaluate(replace(value,messages=tuple(msg(n) for n in range(1,21))))).reason=='count'
    assert asyncio.run(policy.evaluate(replace(value,last_human_at=59,now=60))).reason=='age'
    assert asyncio.run(policy.evaluate(replace(value,messages=(msg(kind='private'),)))).reason=='direct'
    assert asyncio.run(policy.evaluate(replace(value,messages=(msg(kind='private',self_message=True),)))).action=='wait'
