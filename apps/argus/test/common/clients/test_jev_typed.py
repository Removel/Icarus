import asyncio
import pytest
from typesafe_sdk import Noul, Choice, Score, NoulAnswer, ChoiceAnswer, ScoreAnswer, SystemOneResponse, Usage
from apps.argus.src.common.clients.jev_client import JevClient, JevCallError


async def test_typed_multi_question_response_preserved_once():
    questions = {'yes': Noul(), 'kind': Choice(criteria={'a': 'A'}), 'score': Score(criteria=['low', 'high'])}
    response = SystemOneResponse(model='fake', usage=Usage(input_tokens=3), answers={
        'yes': NoulAnswer(noul=.9), 'kind': ChoiceAnswer(choice='a', confidence=1., probabilities={'a': 1.}),
        'score': ScoreAnswer(score=.8, confidence=.8, legend={0: 'low', 1: 'high'}, probabilities={0: .2, 1: .8})})
    async def answer(*_):
        return response
    result = await JevClient(answer=answer).ask(state={}, questions=questions)
    assert result is response and result.answers['score'].score == .8
    assert result.answers['yes'].noul == .9 and result.usage.input_tokens == 3


async def test_wrong_type_exhausts_three_attempts():
    calls = []
    async def answer(*_):
        calls.append(1)
        return SystemOneResponse(model='fake', usage=Usage(), answers={
            'yes': ChoiceAnswer(choice='x', confidence=1., probabilities={'x': 1.})})
    with pytest.raises(JevCallError):
        await JevClient(answer=answer, sleep=lambda _: asyncio.sleep(0)).ask(state={}, questions={'yes': Noul()})
    assert len(calls) == 3
