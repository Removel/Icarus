import asyncio
import pytest
from typesafe_sdk import Choice, ChoiceAnswer, SystemOneResponse, Usage, Noul, NoulAnswer, Score, ScoreAnswer
from apps.argus.src.common.clients.jev_client import JevClient, JevCallError


QUESTIONS = {'should_wake': Choice(criteria={'wake': 'reply', 'silence': 'no reply'})}


def response(choice='wake', name='should_wake'):
    return SystemOneResponse(model='fixture', usage=Usage(), answers={
        name: ChoiceAnswer(choice=choice, confidence=1., probabilities={choice: 1.})})


async def test_named_answers_preserved():
    async def answer(state, questions):
        assert state == {'content': '你好'} and questions == QUESTIONS
        return response()
    result = await JevClient(answer=answer).ask(state={'content': '你好'}, questions=QUESTIONS)
    assert result.answers['should_wake'].choice == 'wake'


@pytest.mark.parametrize('mode', ['transport', 'invalid_choice', 'missing_answer'])
async def test_three_failed_attempts_raise(mode):
    calls, delays = [], []
    async def answer(*_):
        calls.append(True)
        if mode == 'transport':
            raise ConnectionError('offline')
        return response('maybe') if mode == 'invalid_choice' else response(name='other')
    async def sleep(seconds):
        delays.append(seconds)
    with pytest.raises(JevCallError):
        await JevClient(answer=answer, sleep=sleep).ask(state={}, questions=QUESTIONS)
    assert len(calls) == 3 and delays == [0.1, 0.2]


async def test_empty_questions_rejected_without_network():
    async def answer(*_):
        raise AssertionError('No network call expected')
    with pytest.raises(ValueError):
        await JevClient(answer=answer).ask(state={}, questions={})


def test_real_client_requires_api_key():
    with pytest.raises(ValueError):
        JevClient(base_url='https://openrouter.ai/api', model='typesafe/jev-1.13')


async def test_timeout_is_bounded():
    async def answer(*_):
        await asyncio.Event().wait()
    with pytest.raises(JevCallError):
        await JevClient(answer=answer, attempts=1, timeout_seconds=.01).ask(state={}, questions=QUESTIONS)
