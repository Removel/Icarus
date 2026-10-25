"""Bounded semantic choices use only code-owned identities and safe context."""
from uuid import uuid4

import pytest
from typesafe_sdk import ChoiceAnswer, SystemOneResponse, Usage

from apps.argus.test.factories import item_factory
from apps.argus.src.common.clients.jev_client import JevClient


async def test_interleaved_topics_route_individually_without_sender_affinity():
    from apps.argus.src.common.services.session_routing_service import SessionRoutingService
    a, b, waiting = map(str, (uuid4(), uuid4(), uuid4()))
    items = [item_factory(text, sender_id=str(n % 2)) for n, text in enumerate(('A again', 'B', 'yes', 'new'))]
    states = []
    async def answer(state, questions):
        states.append(state)
        return SystemOneResponse(model='fixture', usage=Usage(), answers={name: ChoiceAnswer(
            choice=choice, confidence=1, probabilities={choice: 1}) for name, choice in
            zip(questions, ('candidate_0', 'candidate_1', 'candidate_2', 'new_3'))})
    groups = await SessionRoutingService(JevClient(answer=answer, attempts=1)).route(items, candidates=[
        {'session_id': a, 'summary': 'A', 'last_related_at': '2026-10-01T00:00:00Z', 'has_work': False},
        {'session_id': b, 'summary': 'B', 'last_related_at': None, 'has_work': True},
        {'wake_id': waiting, 'summary': 'not yet submitted', 'chat_id': items[0].chat_id}], recent_context=[item_factory('prior question')])
    assert [group['target_session_id'] for group in groups] == [a, b, None, None]
    assert groups[2]['target_waiting_wake_id'] == waiting
    assert [identifier for group in groups for identifier in group['observation_ids']] == [str(item.id) for item in items]
    assert a not in str(states) and waiting not in str(states) and 'locator' not in str(states)
    assert states[0]['recent'][0]['text'] == 'prior question'


async def test_first_batch_can_split_distinct_new_topics_with_one_bounded_call():
    from apps.argus.src.common.services.session_routing_service import SessionRoutingService
    async def answer(state, questions):
        return SystemOneResponse(model='fixture', usage=Usage(), answers={name: ChoiceAnswer(
            choice=f'new_{n}', confidence=1, probabilities={}) for n, name in enumerate(questions)})
    groups = await SessionRoutingService(JevClient(answer=answer)).route([item_factory('A'), item_factory('B')], candidates=[])
    assert len(groups) == 2


async def test_no_candidates_is_deterministic_but_missing_jev_with_candidates_retries():
    from apps.argus.src.common.services.session_routing_service import SessionRoutingService, RoutingUnavailable
    item = item_factory()
    routing = SessionRoutingService(None)
    groups = await routing.route([item], candidates=[])
    assert groups[0]['target_session_id'] is None
    with pytest.raises(RoutingUnavailable):
        await routing.route([item], candidates=[{'session_id': str(uuid4()), 'summary': 'old'}])


@pytest.mark.parametrize('choice', ['fabricated-uuid', 'candidate_9'])
async def test_unknown_labels_never_turn_into_new_session(choice):
    from apps.argus.src.common.services.session_routing_service import SessionRoutingService, RoutingUnavailable
    async def answer(state, questions):
        return SystemOneResponse(model='fixture', usage=Usage(), answers={name: ChoiceAnswer(
            choice=choice, confidence=1, probabilities={choice: 1}) for name in questions})
    with pytest.raises(RoutingUnavailable):
        await SessionRoutingService(JevClient(answer=answer, attempts=1)).route([item_factory()],
            candidates=[{'session_id': str(uuid4()), 'summary': 'old'}])


async def test_scope_and_sensitive_candidates_are_removed_before_truncation():
    from apps.argus.src.common.services.session_routing_service import SessionRoutingService
    from apps.argus.src.common.services.observation_access_policy import ObservationAccessPolicy
    states = []
    async def answer(state, questions):
        states.append(state)
        return SystemOneResponse(model='fixture', usage=Usage(), answers={name: ChoiceAnswer(
            choice='new_0', confidence=1, probabilities={'new_0': 1}) for name in questions})
    routing = SessionRoutingService(JevClient(answer=answer), policy=ObservationAccessPolicy(['secret']))
    await routing.route([item_factory()], candidates=[
        {'session_id': str(uuid4()), 'summary': 'x' * 3000 + 'secret'},
        {'session_id': str(uuid4()), 'summary': 'other scope', 'chat_id': 'qq:123:group:789'},
        {'session_id': str(uuid4()), 'summary': 'safe'}])
    assert len(states[0]['candidates']) == 1
    assert 'secret' not in str(states) and 'other scope' not in str(states)
