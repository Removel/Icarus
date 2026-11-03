from types import SimpleNamespace

import pytest

from argus_app.model_provider.semantic import SemanticBackend


class Client:
    def __init__(self, route='new', confidence=.9, probabilities=None):
        self.route = route
        self.confidence = confidence
        self.probabilities = probabilities or {route: 1.0}
        self.calls = []

    async def ask(self, *, state, questions):
        self.calls.append((state, questions))
        return SimpleNamespace(answers={
            'route': SimpleNamespace(type='choice', choice=self.route,
                                     confidence=self.confidence, probabilities=self.probabilities),
            # Deliberately disagree: this legacy answer must not control routing.
            'confidence': SimpleNamespace(type='noul', noul=.99),
        })


async def test_discovery_uses_choice_confidence_in_one_request():
    client = Client('channel-1')
    value = await SemanticBackend(client)({'mode': 'discovery', 'criterion': '按测试标准选择Channel归属', 'text': '继续跟进审批',
        'candidates': [{'id': 'channel-1', 'goal': '审批'}]})
    assert value['discovery'] == {'action': 'existing', 'channel_id': 'channel-1'}
    assert value['score'] == .9
    assert len(client.calls) == 1
    assert set(client.calls[0][1]) == {'route'}
    with pytest.raises(ValueError):
        await SemanticBackend(client)({'mode': 'discovery', 'criterion': '按测试标准选择Channel归属', 'text': 'text', 'candidates': []})


async def test_choice_probability_does_not_override_confidence():
    client = Client('new', .64, {'ignore': .18, 'new': .82})
    value = await SemanticBackend(client)({'mode': 'discovery', 'criterion': '按测试标准选择Channel归属', 'text': '请跟进评审', 'candidates': []})
    assert value['score'] == .64
    assert value['selected_route'] == 'new'
    assert value['discovery'] == {'action': 'defer'}
    assert value['defer_reason'] == 'low_confidence'
    assert value['match'] is False


@pytest.mark.parametrize('route,confidence,action,match', [
    ('new', .9, 'new', True),
    ('ignore', .9, 'ignore', False),
    ('new', .7, 'new', True),
    ('new', .6999, 'defer', False),
    ('ignore', .4, 'defer', False),
])
async def test_confidence_gate_uses_native_score(route, confidence, action, match):
    value = await SemanticBackend(Client(route, confidence))({
        'mode': 'discovery', 'criterion': '按测试标准选择Channel归属', 'text': '待判断文本', 'candidates': []})
    assert value['discovery'] == {'action': action}
    assert value['match'] is match
    assert ('defer_reason' in value) is (action == 'defer')


@pytest.mark.parametrize('confidence', [True, False, float('nan'), float('inf'), -.01, 1.01, '0.9', None])
async def test_discovery_rejects_invalid_native_confidence(confidence):
    with pytest.raises(ValueError, match='discovery'):
        await SemanticBackend(Client(confidence=confidence))({
            'mode': 'discovery', 'criterion': '按测试标准选择Channel归属', 'text': '待判断文本', 'candidates': []})


async def test_rule_criterion_and_conversation_reach_decision_model():
    client = Client()
    criterion = '需要 Agent 回应的短对话也建立 Channel。'
    context = {'conversation': {'kind': 'private', 'sender_id': '200'}, 'recent_history': []}
    result = await SemanticBackend(client)({'mode': 'discovery', 'text': '你好',
        'criterion': criterion, 'context': context, 'candidates': []})
    state, questions = client.calls[0]
    assert state['context'] == context
    assert criterion in questions['route'].instructions
    assert questions['route'].criteria['ignore'] != '闲聊、旧结论引用或没有可跟进的新事项'
    assert result['discovery'] == {'action': 'new'}


@pytest.mark.parametrize('arguments', [
    {'criterion': '', 'text': 'hello'},
    {'criterion': '判断是否回应', 'text': 'x' * 16001},
    {'criterion': '判断是否回应', 'text': 'hello', 'context': {'data': 'x' * 17000}},
])
async def test_discovery_rejects_missing_or_oversized_task_before_request(arguments):
    client = Client()
    with pytest.raises(ValueError):
        await SemanticBackend(client)({'mode': 'discovery', 'criterion': '按测试标准选择Channel归属', 'candidates': [], **arguments})
    assert client.calls == []
