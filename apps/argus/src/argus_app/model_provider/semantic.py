"""Reuse Argus's typed Jev request pattern rather than hard-code business rules."""
import math

from typesafe_sdk import AsyncTypeSafeClient, Noul, RetryPolicy


class SemanticBackend:
    def __init__(self, client):
        self.client = client

    async def __call__(self, arguments):
        text = arguments.get('text')
        criterion = arguments.get('criterion', arguments.get('question'))
        if not isinstance(text, str) or not isinstance(criterion, str) or not criterion:
            raise ValueError('semantic text and criterion are required')
        result = await self.client.ask(
            state={'text': text[:16000], 'context': str(arguments.get('context', ''))[:16000]},
            questions={'match': Noul(instructions=criterion[:4000],
                                     criteria={'true': '符合给定判断标准', 'false': '不符合给定判断标准'})})
        score = result.answers['match'].noul
        if isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score) or not 0 <= score <= 1:
            raise ValueError('semantic score must be finite in [0,1]')
        return {'match': score >= .5, 'score': score, 'reason': 'typed semantic judgement'}


class _TypedClient:
    def __init__(self, api_key, base_url=None, model=None):
        self.model = model
        self.sdk = AsyncTypeSafeClient(api_key=api_key, base_url=base_url, model=model,
                                        timeout=5, retry=RetryPolicy(max_retries=0))

    async def ask(self, *, state, questions):
        return await self.sdk.system_one(state, questions, model=self.model)

    async def aclose(self):
        await self.sdk.aclose()


def configured_backend(api_key, base_url=None, model=None):
    client = _TypedClient(api_key, base_url, model)
    return SemanticBackend(client), client
