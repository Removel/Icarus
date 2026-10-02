import asyncio
from datetime import UTC, datetime
from inspect import signature
from apps.argus.src.entity.decision import TaskSnapshot, TaskRelationResult
from apps.argus.src.strategy.qq.qq_action_policy import QQActionPolicy


def test_action_uses_uniform_value_contract():
    from apps.argus.src.entity.policy_inputs import ActionInput
    policy = QQActionPolicy()
    assert tuple(signature(policy.evaluate).parameters) == ('value',)
    snapshot = TaskSnapshot('/work',None,None,(),'absent',datetime.now(UTC))
    result = asyncio.run(policy.evaluate(ActionInput(TaskRelationResult('undetermined','idle'),snapshot)))
    assert result.action == 'submit'
