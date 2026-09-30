"""Typed Jev queries with one bounded retry layer."""
import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typesafe_sdk import AsyncTypeSafeClient, RetryPolicy, SystemOneResponse, ChoiceAnswer


class JevCallError(RuntimeError):
    pass


class JevClient:
    def __init__(self, *, api_key=None, base_url=None, model=None, timeout_seconds=2.0,
                 attempts=3, sleep=asyncio.sleep, answer=None):
        if attempts<1 or timeout_seconds<=0: raise ValueError('Invalid Jev call budget')
        self.timeout_seconds=timeout_seconds; self.attempts=attempts
        self._sleep=sleep; self._answer=answer; self._sdk=None; self._model=model
        if answer is None:
            if not api_key: raise ValueError('api_key is required without an injected answer callable')
            self._sdk=AsyncTypeSafeClient(api_key=api_key,base_url=base_url,model=model,
                                         timeout=timeout_seconds,retry=RetryPolicy(max_retries=0))

    async def _request(self,state,questions,validate):
        last=None
        for attempt in range(self.attempts):
            try:
                operation=self._answer(state,questions) if self._answer else self._sdk.system_one(state,questions,model=self._model)
                response=await asyncio.wait_for(operation,self.timeout_seconds)
                return validate(response)
            except Exception as error:
                last=type(error).__name__
                if attempt<self.attempts-1: await self._sleep(0.1*2**attempt)
        raise JevCallError(f'Jev failed after {self.attempts} attempts ({last})')

    async def ask(self, *, state, questions) -> SystemOneResponse:
        if not questions: raise ValueError('Questions cannot be empty')
        def validate(response):
            if not isinstance(response,SystemOneResponse): raise ValueError('Expected typed Jev response')
            for name,question in questions.items():
                answer=response.answers[name]
                kind=question['type'] if isinstance(question,dict) else question.type
                if answer.type!=kind: raise ValueError('Jev answer type mismatch')
                if isinstance(answer,ChoiceAnswer):
                    criteria=question['criteria'] if isinstance(question,dict) else question.criteria
                    if answer.choice not in criteria: raise ValueError('Unknown Jev choice')
            return response
        return await self._request(state,questions,validate)

    async def aclose(self):
        if self._sdk is not None: await self._sdk.aclose()
