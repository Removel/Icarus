from types import SimpleNamespace
import pytest


async def test_safety_review_denies_uncertain_and_binds_exact_hash():
    try:
        from argus_app.model_provider.safety import RuleSafetyReview
    except ModuleNotFoundError:
        RuleSafetyReview = None
    assert RuleSafetyReview is not None
    calls = []
    class Client:
        async def ask(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(answers={'safe': SimpleNamespace(noul=.5)})
    review = RuleSafetyReview(Client())
    with pytest.raises(ValueError, match='safety'):
        await review.check('def main(event, channel):\n    return {}', 'goal')
    assert len(calls) == 1
    assert 'source' in calls[0]['state']


async def test_safety_review_reuses_exact_source_policy_and_goal_only():
    try:
        from argus_app.model_provider.safety import RuleSafetyReview
    except ModuleNotFoundError:
        RuleSafetyReview = None
    assert RuleSafetyReview is not None
    calls = []
    class Client:
        async def ask(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(answers={'safe': SimpleNamespace(noul=.99)})
    review = RuleSafetyReview(Client())
    source = 'def main(event, channel):\n    return {}'
    first = await review.check(source, 'goal')
    second = await review.check(source, 'goal')
    assert first == second
    assert len(calls) == 1
    await review.check(source, 'different goal')
    assert len(calls) == 2
