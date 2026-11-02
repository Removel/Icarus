"""Provider wire protocol tests; no paid calls or real credentials."""
import json

import httpx2
import pytest

from argus_app.model_provider.semantic import _TypedClient
from typesafe_sdk import Noul


@pytest.mark.parametrize('base,expected_path', [
    ('https://openrouter.ai/api', '/api/alpha/decisions'),
    ('http://127.0.0.1:8009', '/v1/systemone'),
])
async def test_provider_uses_correct_endpoint_with_latest_model(base, expected_path):
    seen = []
    def respond(request):
        seen.append(request)
        assert request.url.path == expected_path
        assert request.headers['Authorization'] == 'Bearer synthetic-test-key'
        body = json.loads(request.content)
        assert body['model'] == 'typesafe/jev-latest'
        assert body['questions']['match'] == {'type': 'noul', 'instructions': 'approved?'}
        return httpx2.Response(200, json={'id': 'test-decision', 'model': 'typesafe/jev-latest',
            'usage': {'input_tokens': 30, 'output_tokens': 1, 'cost': .00001},
            'answers': {'match': {'type': 'noul', 'noul': .95}}})
    try:
        owner = _TypedClient('synthetic-test-key', base, transport=httpx2.MockTransport(respond))
    except TypeError:
        owner = None
    assert owner is not None, 'provider client must support offline wire protocol verification'
    try:
        value = await owner.ask(state={'text': 'approved'}, questions={'match': Noul(instructions='approved?')})
        assert value.answers['match'].noul == .95
        assert len(seen) == 1
    finally:
        await owner.aclose()
