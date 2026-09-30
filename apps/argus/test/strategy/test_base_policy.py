import asyncio
import pytest
from apps.argus.src.strategy.base_policy import BasePolicy
from apps.argus.src.entity.decision import RouteDecision, InterventionResult


class HumanPresent(BasePolicy[tuple[bool, ...], bool]):
    name, version = 'human-present', '1'
    async def evaluate(self, value):
        return any(not item for item in value)


def test_base_is_abstract_and_code_result_is_typed():
    with pytest.raises(TypeError):
        BasePolicy()
    assert asyncio.run(HumanPresent().evaluate((True, False))) is True
    assert InterventionResult('undetermined', 'missing evidence').verdict == 'undetermined'


@pytest.mark.parametrize('action,target', [('steer', None), ('submit', 't'), ('silence', 't'), ('bad', None)])
def test_invalid_route_rejected(action, target):
    with pytest.raises(ValueError):
        RouteDecision(action, 'reason', target)
