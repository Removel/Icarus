import importlib
from types import SimpleNamespace

import pytest


@pytest.mark.asyncio
async def test_semantic_backend_uses_existing_typed_client_contract():
    try:
        Backend = importlib.import_module('argus_app.model_provider.semantic').SemanticBackend
    except ModuleNotFoundError:
        Backend = None
    assert Backend is not None, 'Configured semantic plugin must have a real backend, not fixture-only injection'
    class Client:
        async def ask(self, *, state, questions):
            assert state == {'text': 'approved', 'context': 'wait'}
            assert 'match' in questions
            return SimpleNamespace(answers={'match': SimpleNamespace(noul=.9)})
    backend = Backend(Client())
    assert (await backend({'text': 'approved', 'criterion': 'is approval?', 'context': 'wait'}))['match'] is True


@pytest.mark.asyncio
async def test_semantic_backend_rejects_nonfinite_or_out_of_range_result():
    from argus_app.model_provider.semantic import SemanticBackend
    class Client:
        async def ask(self, **kwargs):
            return SimpleNamespace(answers={'match': SimpleNamespace(noul=float('nan'))})
    with pytest.raises(ValueError, match='score'):
        await SemanticBackend(Client())({'text': 'x', 'criterion': 'y'})


@pytest.mark.asyncio
@pytest.mark.parametrize('base_url,model,expected_base,expected_model', [
    (None, None, 'https://openrouter.ai/api', 'typesafe/jev-latest'),
    ('https://custom.example/api', 'custom-jev', 'https://custom.example/api', 'custom-jev'),
])
async def test_configured_backend_routes_to_openrouter_unless_explicitly_overridden(
        monkeypatch, base_url, model, expected_base, expected_model):
    from argus_app.model_provider import semantic
    seen = []
    class SDK:
        def __init__(self, **kwargs):
            seen.append(kwargs)
        async def system_one(self, state, questions, *, model):
            assert model == expected_model
            return SimpleNamespace(answers={'match': SimpleNamespace(noul=.9)})
        async def aclose(self):
            pass
    monkeypatch.setattr(semantic, 'AsyncTypeSafeClient', SDK)
    backend, owner = semantic.configured_backend('synthetic-key', base_url, model)
    try:
        assert seen[0]['base_url'] == expected_base
        assert seen[0]['model'] == expected_model
        assert (await backend({'text': 'approved', 'criterion': 'approval?'}))['match'] is True
    finally:
        await owner.aclose()
