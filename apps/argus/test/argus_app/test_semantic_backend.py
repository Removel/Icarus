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
