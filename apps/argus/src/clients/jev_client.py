"""Jev client: ask named questions about a state, with bounded retries.

The client is policy-free beyond retry/fallback: callers pass the questions,
the question name to read, the accepted outcomes, and the fallback outcome,
so several judgements over one state stay possible.
"""

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass

from typesafe_sdk import AsyncTypeSafeClient, RetryPolicy

Answers = Mapping[str, str]
QuestionCall = Callable[[dict, Mapping[str, object]], Awaitable[Answers]]


@dataclass(frozen=True)
class Judgement:
    outcome: str
    reason: str | None = None


class JevClient:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        timeout_seconds: float = 2.0,
        attempts: int = 3,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        answer: QuestionCall | None = None,
    ) -> None:
        if attempts < 1:
            raise ValueError("attempts must be at least 1")
        self.timeout_seconds = timeout_seconds
        self.attempts = attempts
        self._sleep = sleep
        self._answer = answer
        self._sdk = None
        if answer is None:
            if not api_key:
                raise ValueError("api_key is required without an injected answer callable")
            self._sdk = AsyncTypeSafeClient(
                api_key=api_key, base_url=base_url, model=model,
                timeout=timeout_seconds, retry=RetryPolicy(max_retries=0),
            )
            self._model = model

    async def decide(
        self, *, state: dict, questions: Mapping[str, object], question: str,
        outcomes: Sequence[str], fallback: str,
    ) -> Judgement:
        if fallback not in outcomes:
            raise ValueError("fallback must be one of the accepted outcomes")
        for attempt in range(self.attempts):
            try:
                answers = await asyncio.wait_for(self._ask(state, questions),
                                                 timeout=self.timeout_seconds)
                choice = answers[question]
                if choice not in outcomes:
                    raise ValueError("Jev returned an outcome outside the accepted set")
                return Judgement(choice)
            except Exception:
                if attempt < self.attempts - 1:
                    await self._sleep(0.1 * (2 ** attempt))
        return Judgement(fallback, "judge_fallback")

    async def _ask(self, state: dict, questions: Mapping[str, object]) -> Answers:
        if self._answer is not None:
            return await self._answer(state, questions)
        response = await self._sdk.system_one(state, questions, model=self._model)
        return {name: answer.choice for name, answer in response.answers.items()}

    async def aclose(self) -> None:
        if self._sdk is not None:
            await self._sdk.aclose()
