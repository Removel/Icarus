"""Internal routes require a service identity before any mutation or content read."""

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from apps.argus.test.common.repositories.test_handoff_repository import bound


async def test_internal_read_requires_token_and_fixed_binding(database):
    from apps.argus.src.common.api.handoff import create_handoff_router
    from apps.argus.src.common.services.handoff_service import HandoffService

    _, repository, initial, wake, binding = await bound(database)
    app = FastAPI()
    app.include_router(create_handoff_router(HandoffService(repository), token='fixture-token'))
    path = f'/api/argus/internal/wakes/{wake.id}/read'
    body = {'protocol_version': 1, 'binding_id': binding, 'observation_id': str(initial.id)}
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        assert (await client.post(path, json=body)).status_code == 401
        accepted = await client.post(path, json=body, headers={'Authorization': 'Bearer fixture-token'})
        assert accepted.status_code == 200
        assert accepted.json()['text'] == 'initial'
        unknown = {**body, 'group_id': 'elsewhere'}
        assert (await client.post(path, json=unknown, headers={'Authorization': 'Bearer fixture-token'})).status_code == 422


async def test_internal_strict_scope_and_identifier_validation(database):
    from apps.argus.src.common.schemas.handoff import BindBody, ReadBody
    from pydantic import ValidationError
    import pytest
    from uuid import uuid4
    with pytest.raises(ValidationError):
        BindBody.model_validate({'protocol_version': 1, 'execution_id': str(uuid4()), 'session_id': str(uuid4()),
            'task_id': str(uuid4()), 'origin_boot_id': str(uuid4()), 'request_fingerprint': '0'*64,
            'scope': {'source': 'qq', 'account_id': '7', 'chat_kind': 'group', 'external_id': '42', 'extra': 'forbidden'}})
    with pytest.raises(ValidationError):
        ReadBody.model_validate({'protocol_version': 1, 'binding_id': str(uuid4()), 'observation_id': 'not-uuid'})
