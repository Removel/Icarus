import asyncio
import pytest
from typesafe_sdk import Noul, Choice, Score, NoulAnswer, ChoiceAnswer, ScoreAnswer, SystemOneResponse, Usage
from apps.argus.src.clients.jev_client import JevClient, JevCallError


def test_typed_multi_question_response_preserved_once():
    calls=[]
    questions={'yes':Noul(), 'kind':Choice(criteria={'a':'A'}), 'score':Score(criteria=['low','high'])}
    response=SystemOneResponse(model='fake',usage=Usage(input_tokens=3),answers={
        'yes':NoulAnswer(noul=0.9),
        'kind':ChoiceAnswer(choice='a',confidence=1.,probabilities={'a':1.}),
        'score':ScoreAnswer(score=0.8,confidence=0.8,legend={0:'low',1:'high'},probabilities={0:0.2,1:0.8})})
    async def answer(*_): calls.append(1); return response
    async def exercise():
        result=await JevClient(answer=answer).ask(state={},questions=questions)
        assert result is response and result.answers['score'].score==0.8
        assert result.answers['yes'].noul==0.9 and result.usage.input_tokens==3
    asyncio.run(exercise()); assert calls==[1]


def test_wrong_type_exhausts_three_attempts():
    calls=[]
    async def answer(*_):
        calls.append(1)
        return SystemOneResponse(model='fake',usage=Usage(),answers={'yes':ChoiceAnswer(choice='x',confidence=1.,probabilities={'x':1.})})
    async def exercise():
        with pytest.raises(JevCallError):
            await JevClient(answer=answer,sleep=lambda _:asyncio.sleep(0)).ask(state={},questions={'yes':Noul()})
    asyncio.run(exercise()); assert len(calls)==3
