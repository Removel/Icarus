from apps.argus.src.entity.policy_inputs import BatchTimingInput
import asyncio
from datetime import UTC, datetime
import pytest
from apps.argus.src.entity.evidence import RecentActivity
from apps.argus.src.strategy.qq.qq_batch_policy import QQBatchPolicy
from apps.argus.src.handler.qq_handler import QQHandler


def message():
    return QQHandler('10000').parse(dict(post_type='message',self_id=10000,time=1,message_id=1,message_type='group',group_id=42,user_id=7,message='hi'))


@pytest.mark.parametrize('count,delay',[(0,0),(1,0),(2,15),(3,30),(6,240),(100000,300)])
def test_backoff_is_bounded_and_does_not_slide(count, delay):
    activity = RecentActivity(last_judged_at=datetime.fromtimestamp(100,UTC),consecutive_no_action=count)
    result = asyncio.run(QQBatchPolicy(quiet_seconds=1).evaluate(BatchTimingInput(messages=(message(),),opened_at=99,last_human_at=99,now=100,human_pending_count=1,has_strong_signal=False,activity=activity)))
    assert result.deadline == 100 + delay
    assert result.action == ('close' if delay == 0 else 'wait')


def test_collect_limit_does_not_bypass_backoff_but_six_humans_do():
    activity = RecentActivity(last_judged_at=datetime.fromtimestamp(100,UTC),consecutive_no_action=100)
    kwargs = dict(messages=(message(),),opened_at=0,last_human_at=110,now=112,human_pending_count=1,has_strong_signal=False,activity=activity)
    result = asyncio.run(QQBatchPolicy().evaluate(BatchTimingInput(**kwargs)))
    assert result.action == 'wait' and result.deadline == 400
    kwargs['human_pending_count'] = 6
    assert asyncio.run(QQBatchPolicy().evaluate(BatchTimingInput(**kwargs))).action == 'close'


def test_recent_start_spacing_combines_by_max_and_signal_bypasses():
    activity=RecentActivity(last_judged_at=datetime.fromtimestamp(100,UTC),consecutive_no_action=3,
        last_submit_at=datetime.fromtimestamp(110,UTC),submissions_in_window=2)
    kwargs=dict(messages=(message(),),opened_at=110,last_human_at=110,now=112,
        human_pending_count=1,has_strong_signal=False,activity=activity)
    result=asyncio.run(QQBatchPolicy().evaluate(BatchTimingInput(**kwargs)))
    assert result.deadline==140  # max(116,130,140), not multiplied.
    kwargs['has_strong_signal']=True
    assert asyncio.run(QQBatchPolicy().evaluate(BatchTimingInput(**kwargs))).action=='close'
