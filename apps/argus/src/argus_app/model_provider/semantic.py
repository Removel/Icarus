"""Reuse Argus's typed Jev request pattern rather than hard-code business rules."""
import math
import json
from urllib.parse import urlsplit
import httpx2

from typesafe_sdk import AsyncTypeSafeClient, Noul, Choice, RetryPolicy

# Match the repository's established JEV route; never leak into the SDK's unrelated defaults.
DEFAULT_JEV_BASE_URL = 'https://openrouter.ai/api'
DEFAULT_JEV_MODEL = '~typesafe/jev-latest'


def validate_discovery_task(text, criterion, context):
    if not isinstance(text, str) or len(text) > 16000:
        raise ValueError('Invalid discovery text or input_too_large')
    if not isinstance(criterion, str) or not criterion.strip() or len(criterion) > 4000:
        raise ValueError('Invalid discovery criterion')
    if not isinstance(context, dict) or len(json.dumps(context, ensure_ascii=False, allow_nan=False).encode()) > 16384:
        raise ValueError('Invalid discovery context or input_too_large')


class SemanticBackend:
    def __init__(self, client):
        self.client = client

    async def __call__(self, arguments):
        if arguments.get('mode') == 'discovery':
            return await self.discover(arguments)
        text = arguments.get('text')
        criterion = arguments.get('criterion', arguments.get('question'))
        if not isinstance(text, str) or not isinstance(criterion, str) or not criterion:
            raise ValueError('semantic text and criterion are required')
        result = await self.client.ask(
            state={'text': text[:16000], 'context': (arguments.get('context', '') if isinstance(arguments.get('context', ''), str)
                   else json.dumps(arguments.get('context'), ensure_ascii=False, allow_nan=False))[:16000]},
            questions={'match': Noul(instructions=criterion[:4000],
                                     criteria={'true': '符合给定判断标准', 'false': '不符合给定判断标准'})})
        score = result.answers['match'].noul
        if isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score) or not 0 <= score <= 1:
            raise ValueError('semantic score must be finite in [0,1]')
        return {'match': score >= .5, 'score': score, 'reason': 'typed semantic judgement'}


    async def discover(self, arguments):
        candidates = arguments.get('candidates', [])
        criterion, context = arguments.get('criterion'), arguments.get('context', {})
        validate_discovery_task(arguments.get('text'), criterion, context)
        if not isinstance(candidates, list) or len(candidates) > 20:
            raise ValueError('Invalid discovery input')
        choices = {'ignore': '按给定标准无需处理且与候选无关',
                   'new': '按给定标准需要新建 Channel，不属于已有候选'}
        for candidate in candidates:
            if (not isinstance(candidate, dict) or not isinstance(candidate.get('id'), str)
                    or not candidate['id'] or candidate['id'] in choices or candidate['id'] == 'defer'):
                raise ValueError('Invalid discovery candidate')
            encoded = json.dumps(candidate, ensure_ascii=False, allow_nan=False)
            if len(encoded.encode()) > 4096:
                raise ValueError('Invalid discovery candidate size')
            choices[candidate['id']] = encoded
        result = await self.client.ask(
            state={'text': arguments['text'], 'context': context, 'candidates': candidates},
            questions={'route': Choice(instructions=criterion, criteria=choices)})
        route, score = result.answers['route'].choice, result.answers['route'].confidence
        if route not in choices or isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score) or not 0 <= score <= 1:
            raise ValueError('Invalid discovery answer')
        discovery = {'action': 'defer'} if score < .7 else {'action': route if route in {'ignore', 'new'} else 'existing'}
        if discovery['action'] == 'existing':
            discovery['channel_id'] = route
        value = {'match': discovery['action'] in {'new', 'existing'}, 'score': score,
                 'reason': 'typed discovery judgement', 'discovery': discovery, 'selected_route': route}
        if discovery['action'] == 'defer':
            value['defer_reason'] = 'low_confidence'
        return value


class _TypedClient:
    def __init__(self, api_key, base_url=None, model=None, *, transport=None):
        self.model = model or DEFAULT_JEV_MODEL
        self.base_url = (base_url or DEFAULT_JEV_BASE_URL).rstrip('/')
        parsed = urlsplit(self.base_url)
        if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError('JEV endpoint must be an HTTP(S) URL without embedded credentials/query/fragment')
        if parsed.hostname == 'openrouter.ai' and parsed.scheme != 'https':
            raise ValueError('OpenRouter requires HTTPS')
        self.endpoint = self.base_url + ('/alpha/decisions' if parsed.hostname == 'openrouter.ai' else '/v1/systemone')
        async def provider_endpoint(request):
            # Keep native /v1/systemone for self-hosted JEV; only OpenRouter has
            # the Decisions wire endpoint. Preserve auth/body and all SDK validation.
            if parsed.hostname == 'openrouter.ai' and str(request.url) == self.base_url + '/v1/systemone':
                request.url = httpx2.URL(self.endpoint)
        http = httpx2.AsyncClient(timeout=5, transport=transport,
                                 event_hooks={'request': [provider_endpoint]})
        self.sdk = AsyncTypeSafeClient(api_key=api_key, base_url=self.base_url, model=self.model,
                                        http_client=http, retry=RetryPolicy(max_retries=0))

    async def ask(self, *, state, questions):
        return await self.sdk.system_one(state, questions, model=self.model)

    async def aclose(self):
        await self.sdk.aclose()


def configured_backend(api_key, base_url=None, model=None):
    client = _TypedClient(api_key, base_url, model)
    return SemanticBackend(client), client
