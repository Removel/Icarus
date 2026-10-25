import pytest
from pathlib import Path

from apps.argus.test.factories import item_factory


def rules():
    from apps.argus.src.common.services.rule_service import load_rule_set
    from apps.argus.src.surfaces.qq.services.fact_service import QQFactService
    return load_rule_set(Path(__file__).resolve().parents[3] / 'rules/qq.yaml',
                         known_facts=set(QQFactService.known_facts))


def service(**changes):
    from apps.argus.src.common.services.judgement_service import JudgementService
    from apps.argus.src.surfaces.qq.services.fact_service import QQFactService
    return JudgementService(rules(), QQFactService(), None, sensitive_keywords=('secret',), **changes)


@pytest.mark.parametrize('parts', [
    [('image', 'original-ref')], [('text', '@123'), ('image', 'original-ref')],
    [('text', '   ')], [('text', '😀！'), ('other', 'face')],
])
async def test_unreadable_direct_messages_do_not_wake_or_lose_media(parts):
    from apps.argus.src.common.schemas.observation import ContentPart
    item = item_factory(content=tuple(ContentPart(kind=kind, value=value) for kind, value in parts),
        locator={'chat_kind': 'private'}, detail={'mentions_bot': True})
    result = await service().judge([item], permission_level=2)
    assert result.decision == 'ignore'
    assert result.ignored[0].reason == 'unreadable_content'
    assert [(part.kind, part.value) for part in item.content] == parts


async def test_unreadable_item_cannot_piggyback_on_mixed_text_mention():
    from apps.argus.src.common.schemas.observation import ContentPart
    image = item_factory(content=(ContentPart(kind='image', value='original-ref'),))
    text = item_factory(content=(ContentPart(kind='text', value='@123 help'),
        ContentPart(kind='image', value='another-ref')), detail={'mentions_bot': True})
    result = await service().judge([image, text], permission_level=2)
    assert result.decision == 'wake'
    assert [value.item.id for value in result.selected] == [text.id]
    assert result.selected[0].excerpt == 'help'


async def test_private_and_mention_wake_without_jev():
    for item in [item_factory(locator={'chat_kind': 'private'}),
                 item_factory(detail={'mentions_bot': True})]:
        outcome = await service().judge([item], permission_level=2)
        assert outcome.decision == 'wake'
        assert outcome.rule_id in {'private_message', 'mention_bot'}


async def test_long_first_item_does_not_consume_tail():
    head, tail = item_factory('a' * 25000), item_factory('@Icarus', strong_signal=True)
    result = await service().judge([head, tail], permission_level=2)
    assert [value.item.id for value in result.selected] == [head.id]
    assert len(result.selected[0].excerpt) == 20000 and result.selected[0].truncated
    assert all(tail.id not in value.item_ids for value in result.ignored)


async def test_sensitive_tail_checked_before_excerpt_and_valid_item_survives():
    sensitive = item_factory('a' * 25000 + 'secret')
    valid = item_factory('help', detail={'mentions_bot': True})
    result = await service().judge([sensitive, valid], permission_level=2)
    assert result.decision == 'wake'
    assert [value.item.id for value in result.selected] == [valid.id]
    assert result.ignored[0].item_ids == (sensitive.id,)


async def test_self_item_cannot_trigger_valid_group():
    value = item_factory('@Icarus', is_self=True, detail={'mentions_bot': True})
    result = await service().judge([value, item_factory('hi')], permission_level=2)
    assert result.decision == 'ignore'
    assert result.ignored[0].reason == 'self_message'


async def test_semantic_wire_asks_two_yes_no_questions(monkeypatch):
    import httpx2
    import json
    from typesafe_sdk import AsyncTypeSafeClient, RetryPolicy
    from apps.argus.src.common.clients.jev_client import JevClient
    from apps.argus.src.common.services.judgement_service import JudgementService
    from apps.argus.src.surfaces.qq.services.fact_service import QQFactService

    calls = []

    def handler(request):
        body = json.loads(request.content)
        calls.append(body)
        for name in ('can_speak', 'speak_now'):
            question = body['questions'][name]
            if not isinstance(question.get('instructions'), str) or not question['instructions'].strip():
                return httpx2.Response(400, json={'error': {'message': 'instructions required'}})
        return httpx2.Response(200, json={'model': 'typesafe/jev-1.13',
            'usage': {'input_tokens': 10, 'output_tokens': 1},
            'answers': {'can_speak': {'type': 'noul', 'noul': .9},
                        'speak_now': {'type': 'noul', 'noul': .8}}})

    client = AsyncTypeSafeClient(api_key='fixture', base_url='https://openrouter.example/api',
        transport=httpx2.MockTransport(handler), retry=RetryPolicy(max_retries=0))
    monkeypatch.setattr('apps.argus.src.common.clients.jev_client.AsyncTypeSafeClient', lambda **kwargs: client)
    jev = JevClient(api_key='fixture', base_url='https://openrouter.example/api',
                    model='typesafe/jev-1.13', attempts=1)
    try:
        outcome = await JudgementService(rules(), QQFactService(), jev).judge(
            [item_factory('怎么做')], permission_level=2)
        assert outcome.decision == 'wake' and outcome.rule_id == 'speak'
        assert outcome.decision_detail['can_speak'] == .9
        assert outcome.decision_detail['speak_now'] == .8
        assert len(calls) == 1
        can, now = calls[0]['questions']['can_speak'], calls[0]['questions']['speak_now']
        assert can['type'] == 'noul' and now['type'] == 'noul'
        assert set(can['criteria']) == {'true', 'false'}
        assert set(now['criteria']) == {'true', 'false'}
        assert 'Icarus' in can['instructions'] and '合适' in can['instructions']
        assert '现在' in now['instructions']
        assert set(calls[0]) >= {'state', 'model', 'questions'}
        assert 'window' in calls[0]['state'] and 'items' not in calls[0]['state']
    finally:
        await jev.aclose()


async def test_semantic_input_removes_sensitive_history_and_accounts():
    from typesafe_sdk import NoulAnswer, SystemOneResponse, Usage
    from apps.argus.src.common.clients.jev_client import JevClient
    from apps.argus.src.common.services.judgement_service import JudgementService
    from apps.argus.src.surfaces.qq.services.fact_service import QQFactService
    states = []
    async def answer(state, questions):
        states.append(state)
        return SystemOneResponse(model='fake', usage=Usage(), answers={
            'can_speak': NoulAnswer(noul=.9), 'speak_now': NoulAnswer(noul=.8)})
    judge = JudgementService(rules(), QQFactService(), JevClient(answer=answer),
        sensitive_keywords=('secret',), recent_chars=4)
    result = await judge.judge([item_factory('怎么做')], permission_level=2,
        recent_context=[item_factory('secret'), item_factory('123456789')])
    assert result.decision == 'wake' and result.rule_id == 'speak'
    window = states[0]['window']
    assert len(window) == 2
    assert window[0]['text'] == '1234' and window[0]['current'] is False
    assert window[1]['text'] == '怎么做' and window[1]['current'] is True
    assert 'chat_id' not in states[0] and 'locator' not in str(states[0])
    assert 'secret' not in str(states[0])
    assert 'sender_id' not in str(states[0])


async def test_window_includes_self_messages_and_marks_current():
    from typesafe_sdk import NoulAnswer, SystemOneResponse, Usage
    from apps.argus.src.common.clients.jev_client import JevClient
    from apps.argus.src.common.services.judgement_service import JudgementService
    from apps.argus.src.surfaces.qq.services.fact_service import QQFactService
    states = []
    async def answer(state, questions):
        states.append(state)
        return SystemOneResponse(model='fake', usage=Usage(), answers={
            'can_speak': NoulAnswer(noul=.9), 'speak_now': NoulAnswer(noul=.8)})
    judge = JudgementService(rules(), QQFactService(), JevClient(answer=answer))
    await judge.judge([item_factory('能介绍一下你自己吗', sender_name='鱼梦')], permission_level=2,
        recent_context=[item_factory('上一句', sender_name='鱼梦'),
                        item_factory('Icarus 的回复', is_self=True, sender_name='Icarus')])
    window = states[0]['window']
    assert [value['text'] for value in window] == ['上一句', 'Icarus 的回复', '能介绍一下你自己吗']
    assert [value['is_self'] for value in window] == [False, True, False]
    assert [value['current'] for value in window] == [False, False, True]
    assert window[1]['sender'] == 'Icarus'


async def test_can_speak_no_stays_silent():
    from typesafe_sdk import NoulAnswer, SystemOneResponse, Usage
    from apps.argus.src.common.clients.jev_client import JevClient
    from apps.argus.src.common.services.judgement_service import JudgementService
    from apps.argus.src.surfaces.qq.services.fact_service import QQFactService
    async def answer(state, questions):
        return SystemOneResponse(model='fake', usage=Usage(), answers={
            'can_speak': NoulAnswer(noul=.3), 'speak_now': NoulAnswer(noul=.9)})
    judge = JudgementService(rules(), QQFactService(), JevClient(answer=answer))
    result = await judge.judge([item_factory('随便聊聊')], permission_level=2)
    assert result.decision == 'ignore'
    assert result.rule_id == 'stay_silent'
    assert result.reason == '现在不适合 Icarus 说话'
    assert result.decision_detail['can_speak'] == .3
    assert result.decision_detail['speak_now'] == .9
    assert 'choice' not in result.decision_detail


async def test_speak_now_no_stays_silent():
    from typesafe_sdk import NoulAnswer, SystemOneResponse, Usage
    from apps.argus.src.common.clients.jev_client import JevClient
    from apps.argus.src.common.services.judgement_service import JudgementService
    from apps.argus.src.surfaces.qq.services.fact_service import QQFactService
    async def answer(state, questions):
        return SystemOneResponse(model='fake', usage=Usage(), answers={
            'can_speak': NoulAnswer(noul=.9), 'speak_now': NoulAnswer(noul=.3)})
    judge = JudgementService(rules(), QQFactService(), JevClient(answer=answer))
    result = await judge.judge([item_factory('这个话题可以接')], permission_level=2)
    assert result.decision == 'ignore'
    assert result.rule_id == 'stay_silent'
    assert result.decision_detail['can_speak'] == .9
    assert result.decision_detail['speak_now'] == .3


async def test_proactive_reply_wakes_without_mention():
    from typesafe_sdk import NoulAnswer, SystemOneResponse, Usage
    from apps.argus.src.common.clients.jev_client import JevClient
    from apps.argus.src.common.services.judgement_service import JudgementService
    from apps.argus.src.surfaces.qq.services.fact_service import QQFactService
    async def answer(state, questions):
        return SystemOneResponse(model='fake', usage=Usage(), answers={
            'can_speak': NoulAnswer(noul=.8), 'speak_now': NoulAnswer(noul=.7)})
    judge = JudgementService(rules(), QQFactService(), JevClient(answer=answer))
    result = await judge.judge([item_factory('这个方案我觉得还可以再改改')], permission_level=2)
    assert result.decision == 'wake' and result.rule_id == 'speak'


async def test_semantic_state_has_only_window_fields():
    from typesafe_sdk import NoulAnswer, SystemOneResponse, Usage
    from apps.argus.src.common.clients.jev_client import JevClient
    from apps.argus.src.common.services.judgement_service import JudgementService
    from apps.argus.src.surfaces.qq.services.fact_service import QQFactService
    states = []
    async def answer(state, questions):
        states.append(state)
        return SystemOneResponse(model='fake', usage=Usage(), answers={
            'can_speak': NoulAnswer(noul=.9), 'speak_now': NoulAnswer(noul=.8)})
    judge = JudgementService(rules(), QQFactService(), JevClient(answer=answer))
    await judge.judge([item_factory('hello', sender_name='鱼梦')], permission_level=2)
    assert set(states[0]) == {'scope_kind', 'now', 'window'}
    assert set(states[0]['window'][0]) == {'sender', 'is_self', 'text', 'sent_at', 'current'}
    assert 'sender_id' not in str(states[0])


async def test_display_names_cannot_bypass_filter_or_total_text_budget():
    from typesafe_sdk import NoulAnswer, SystemOneResponse, Usage
    from apps.argus.src.common.clients.jev_client import JevClient
    from apps.argus.src.common.services.judgement_service import JudgementService
    from apps.argus.src.surfaces.qq.services.fact_service import QQFactService
    states = []
    async def answer(state, questions):
        states.append(state)
        return SystemOneResponse(model='fake', usage=Usage(), answers={
            'can_speak': NoulAnswer(noul=.9), 'speak_now': NoulAnswer(noul=.8)})
    judge = JudgementService(rules(), QQFactService(), JevClient(answer=answer),
        sensitive_keywords=('secret',), max_chars=20, recent_chars=10)
    restricted = await judge.judge([item_factory('hello', sender_name='secret' + 'n' * 100000)],
        permission_level=2, recent_context=[item_factory('old', sender_name='n' * 100000)])
    assert states == []
    assert restricted.ignored[0].reason == 'sensitive_content'
    await judge.judge([item_factory('hello', sender_name='n' * 100000)], permission_level=2,
        recent_context=[item_factory('old', sender_name='n' * 100000)])
    assert states and 'secret' not in str(states[0])
    window = states[0]['window']
    current = [value for value in window if value['current']]
    recent = [value for value in window if not value['current']]
    assert sum(len(value['text']) + len(value['sender']) for value in current) <= 20
    assert sum(len(value['text']) + len(value['sender']) for value in recent) <= 10


async def test_nonfinite_semantic_values_are_silent_not_persisted():
    from typesafe_sdk import NoulAnswer, SystemOneResponse, Usage
    from apps.argus.src.common.clients.jev_client import JevClient
    from apps.argus.src.common.services.judgement_service import JudgementService
    from apps.argus.src.surfaces.qq.services.fact_service import QQFactService
    for invalid in (float('nan'), float('inf')):
        async def answer(state, questions):
            return SystemOneResponse(model='fake', usage=Usage(), answers={
                'can_speak': NoulAnswer(noul=invalid), 'speak_now': NoulAnswer(noul=.5)})
        judge = JudgementService(rules(), QQFactService(), JevClient(answer=answer))
        result = await judge.judge([item_factory('question')], permission_level=2)
        assert result.decision == 'ignore' and result.reason == 'jev_failed'
        assert result.decision_detail['failed'] is True


async def test_semantic_failure_is_silent_and_no_raw_text_in_detail():
    from apps.argus.src.common.clients.jev_client import JevClient
    from apps.argus.src.common.services.judgement_service import JudgementService
    from apps.argus.src.surfaces.qq.services.fact_service import QQFactService
    async def fail(*_):
        raise TimeoutError('private text')
    judge = JudgementService(rules(), QQFactService(), JevClient(answer=fail, attempts=1))
    result = await judge.judge([item_factory('private source text')], permission_level=2)
    assert result.decision == 'ignore' and result.reason == 'jev_failed'
    assert 'private' not in str(result.decision_detail)


def test_unknown_rule_operator_rejected(tmp_path):
    from apps.argus.src.common.services.rule_service import load_rule_set, RuleValidationError
    path = tmp_path / 'rules.yaml'
    path.write_text('source: qq\nversion: 1\ndefault: {decision: ignore, reason: no}\nrules:\n'
                    '  - {id: bad, when: {text: {regex: x}}, decision: wake, reason: test}\n')
    with pytest.raises(RuleValidationError):
        load_rule_set(path, known_facts={'text'})
