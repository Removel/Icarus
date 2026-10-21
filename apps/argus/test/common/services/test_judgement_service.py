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


async def test_semantic_input_removes_sensitive_history_and_accounts():
    from typesafe_sdk import ChoiceAnswer, SystemOneResponse, Usage
    from apps.argus.src.common.clients.jev_client import JevClient
    from apps.argus.src.common.services.judgement_service import JudgementService
    from apps.argus.src.surfaces.qq.services.fact_service import QQFactService
    states = []
    async def answer(state, questions):
        states.append(state)
        return SystemOneResponse(model='fake', usage=Usage(), answers={'intent': ChoiceAnswer(
            choice='genuine_question', confidence=.9, probabilities={'genuine_question': .9, 'none': .1})})
    judge = JudgementService(rules(), QQFactService(), JevClient(answer=answer),
        sensitive_keywords=('secret',), recent_chars=4)
    result = await judge.judge([item_factory('怎么做')], permission_level=2,
        recent_context=[item_factory('secret'), item_factory('123456789')])
    assert result.decision == 'wake' and result.rule_id == 'genuine_question'
    assert states[0]['recent'][0]['text'] == '1234' and len(states[0]['recent']) == 1
    assert 'chat_id' not in states[0] and 'locator' not in str(states[0])
    assert 'secret' not in str(states[0])


async def test_display_names_cannot_bypass_filter_or_total_text_budget():
    from typesafe_sdk import ChoiceAnswer, SystemOneResponse, Usage
    from apps.argus.src.common.clients.jev_client import JevClient
    from apps.argus.src.common.services.judgement_service import JudgementService
    from apps.argus.src.surfaces.qq.services.fact_service import QQFactService
    states = []
    async def answer(state, questions):
        states.append(state)
        return SystemOneResponse(model='fake', usage=Usage(), answers={'intent': ChoiceAnswer(
            choice='none', confidence=1, probabilities={'none': 1})})
    judge = JudgementService(rules(), QQFactService(), JevClient(answer=answer),
        sensitive_keywords=('secret',), max_chars=20, recent_chars=10)
    await judge.judge([item_factory('hello', sender_name='secret' + 'n' * 100000)],
        permission_level=2, recent_context=[item_factory('old', sender_name='n' * 100000)])
    assert states and 'secret' not in str(states[0])
    assert sum(len(value['text']) + len(value.get('sender_name') or '') for value in states[0]['items']) <= 20
    assert sum(len(value['text']) + len(value.get('sender_name') or '') for value in states[0]['recent']) <= 10


async def test_nonfinite_semantic_values_are_silent_not_persisted():
    from typesafe_sdk import ChoiceAnswer, SystemOneResponse, Usage
    from apps.argus.src.common.clients.jev_client import JevClient
    from apps.argus.src.common.services.judgement_service import JudgementService
    from apps.argus.src.surfaces.qq.services.fact_service import QQFactService
    import math
    for invalid in (float('nan'), float('inf')):
        async def answer(state, questions):
            return SystemOneResponse(model='fake', usage=Usage(), answers={'intent': ChoiceAnswer(
                choice='genuine_question', confidence=invalid, probabilities={'genuine_question': invalid})})
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
