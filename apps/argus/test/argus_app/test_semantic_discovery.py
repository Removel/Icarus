from types import SimpleNamespace
import pytest

from argus_app.model_provider.semantic import SemanticBackend


async def test_discovery_uses_one_typed_request_and_rejects_unknown_candidate():
    calls = []
    class Client:
        async def ask(self, *, state, questions):
            calls.append((state, questions))
            return SimpleNamespace(answers={'route': SimpleNamespace(choice='channel-1'),
                                           'confidence': SimpleNamespace(noul=.9)})
    value = await SemanticBackend(Client())({'mode': 'discovery', 'text': '继续跟进审批',
        'candidates': [{'id': 'channel-1', 'goal': '审批'}]})
    assert value['discovery'] == {'action': 'existing', 'channel_id': 'channel-1'}
    assert len(calls) == 1
    with pytest.raises(ValueError):
        await SemanticBackend(Client())({'mode': 'discovery', 'text': 'text', 'candidates': []})


async def test_low_confidence_discovery_defers_instead_of_creating():
    class Client:
        async def ask(self, **kwargs):
            return SimpleNamespace(answers={'route': SimpleNamespace(choice='new'),
                                           'confidence': SimpleNamespace(noul=.4)})
    value = await SemanticBackend(Client())({'mode': 'discovery', 'text': '含糊消息', 'candidates': []})
    assert value['discovery'] == {'action': 'defer'}
    assert value['match'] is False
