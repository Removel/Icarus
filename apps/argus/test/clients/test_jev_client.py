import asyncio
import pytest
from typesafe_sdk import Choice, ChoiceAnswer, SystemOneResponse, Usage
from apps.argus.src.clients.jev_client import JevClient, JevCallError


QUESTIONS = {'should_wake': Choice(criteria={'wake': 'reply', 'silence': 'no reply'})}


def response(choice='wake', name='should_wake'):
    return SystemOneResponse(model='fixture', usage=Usage(), answers={
        name: ChoiceAnswer(choice=choice, confidence=1., probabilities={choice: 1.})})


def test_named_answers_are_preserved():
    calls = []
    async def answer(state, questions):
        calls.append((state, questions))
        return response()
    async def exercise():
        result = await JevClient(answer=answer).ask(state={'content': '你好'}, questions=QUESTIONS)
        assert result.answers['should_wake'].choice == 'wake'
        assert calls == [({'content': '你好'}, QUESTIONS)]
    asyncio.run(exercise())


@pytest.mark.parametrize('mode', ['transport', 'invalid_choice', 'missing_answer'])
def test_three_failed_attempts_raise_for_graph_fallback(mode):
    calls, delays = [], []
    async def answer(*_):
        calls.append(True)
        if mode == 'transport':
            raise ConnectionError('offline')
        return response('maybe') if mode == 'invalid_choice' else response(name='other')
    async def sleep(seconds):
        delays.append(seconds)
    async def exercise():
        with pytest.raises(JevCallError):
            await JevClient(answer=answer, sleep=sleep).ask(state={}, questions=QUESTIONS)
    asyncio.run(exercise())
    assert len(calls) == 3 and delays == [0.1, 0.2]


def test_empty_question_set_rejected_without_network_call():
    async def answer(*_):
        raise AssertionError('No network call expected')
    async def exercise():
        with pytest.raises(ValueError):
            await JevClient(answer=answer).ask(state={}, questions={})
    asyncio.run(exercise())


def test_real_client_requires_api_key():
    with pytest.raises(ValueError):
        JevClient(base_url='https://openrouter.ai/api', model='typesafe/jev-1.13')
