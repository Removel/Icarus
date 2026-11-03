"""Rule-owned response decisions; models are deterministic boundary doubles here."""
from uuid import uuid4
import pytest

from argus_app.contracts.models import Observation, DiscoveryDecision
from argus_app.plugins.workflow_rules.discovery import DEFAULT_DISCOVERY_RULE, INITIAL_CHANNEL_RULE
from argus_app.rule_sdk import RuleEngine


def item(text):
    return Observation(id=str(uuid4()), source_key='qq:100:private:200', message_id='m',
        revision=1, observed_at='2026-11-03T00:00:00+00:00', kind='message.observed',
        content={'text': text, 'sender_id': '200', 'message_type': 'private'},
        detail={'protocol': 'onebot', 'self_id': '100', 'peer_id': '200', 'is_self': False})


async def test_default_rule_sends_response_criterion_and_uses_bounded_history():
    calls = []
    async def call(name, arguments):
        calls.append((name, arguments))
        if name == 'history':
            assert arguments['limit'] <= 8
            return {'observations': []}
        assert name == 'semantic'
        assert 'Channel' in arguments.get('criterion', '')
        assert '回应' in arguments.get('criterion', '')
        return {'match': True, 'reason': 'direct greeting needs response', 'discovery': {'action': 'new'}}
    result = await RuleEngine(timeout=10).run(DEFAULT_DISCOVERY_RULE, item('你好').model_dump(),
        {'conversation': {'kind': 'private'}, 'candidates': []}, call)
    assert result['action'] == 'new'
    assert [c[0] for c in calls] == ['history', 'semantic']


async def test_initial_active_rule_can_wake_on_related_second_message():
    async def call(name, arguments):
        if name == 'history':
            return {'observations': []}
        assert '回应' in arguments['criterion']
        return {'match': True, 'score': .9, 'reason': 'needs response'}
    result = await RuleEngine(timeout=10).run(INITIAL_CHANNEL_RULE, item('再说详细一点').model_dump(),
        {'id': str(uuid4()), 'goal': '短对话', 'progress': '已打招呼'}, call)
    assert result['passed'] is True


def test_conversation_context_keeps_trusted_identity_and_excludes_runtime_detail():
    from argus_app.plugins.workflow_rules import discovery
    assert callable(getattr(discovery, 'semantic_context', None)), 'trusted context builder missing'
    current = item('你好')
    current.detail['binding_secret'] = 'must-not-leak'
    context = discovery.semantic_context(current, [current.model_dump()])
    assert context['conversation']['source_key'] == 'qq:100:private:200'
    assert context['conversation']['peer_id'] == '200'
    assert context['conversation']['is_self'] is False
    assert context['recent_history'][0]['text'] == '你好'
    assert 'must-not-leak' not in str(context)
    assert 'detail' not in context['recent_history'][0]


def test_only_defer_can_request_deeper_semantic_review():
    value = DiscoveryDecision(action='defer', reason='ambiguous', review_required=True)
    assert value.review_required is True
    with pytest.raises(ValueError):
        DiscoveryDecision(action='new', reason='illegal flag', review_required=True)
