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


async def test_v2_available_and_session_pending_are_authenticated_bodyless_and_version_pinned(database):
    from apps.argus.src.common.api.handoff import create_handoff_router
    from apps.argus.src.common.services.handoff_service import HandoffService
    from apps.argus.test.common.repositories.test_semantic_assignment import semantic_bound, SCOPE
    repo, repository, first, wake, binding, target = await semantic_bound(database)
    app = FastAPI()
    app.include_router(create_handoff_router(HandoffService(repository), token='fixture-token'))
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        path = f'/api/argus/internal/sessions/{target}/pending'
        body = {'protocol_version': 2, 'scope': SCOPE}
        assert (await client.post(path, json=body)).status_code == 401
        headers = {'Authorization': 'Bearer fixture-token'}
        response = await client.post(path, json=body, headers=headers)
        assert response.status_code == 200 and response.json()['pending_count'] == 1
        assert set(response.json()) == {'protocol_version', 'pending_count', 'last_related_at'}
        available = f'/api/argus/internal/wakes/{wake.id}/available'
        result = await client.post(available, json={'protocol_version': 2, 'binding_id': binding}, headers=headers)
        assert result.status_code == 200 and result.json()['pending_count'] == 1
        mismatch = await client.post(available, json={'protocol_version': 1, 'binding_id': binding}, headers=headers)
        assert mismatch.status_code == 409
        assert (await repo.get_item(first.id)).exposed_wake_id is None


async def test_authenticated_v2_errors_keep_validated_version(database, monkeypatch):
    from uuid import uuid4
    from sqlalchemy.exc import SQLAlchemyError
    from apps.argus.src.common.api.handoff import create_handoff_router
    from apps.argus.src.common.services.handoff_service import HandoffService
    from apps.argus.test.common.repositories.test_semantic_assignment import semantic_bound
    _, repository, _, wake, binding, _ = await semantic_bound(database)
    service = HandoffService(repository)
    app = FastAPI()
    app.include_router(create_handoff_router(service, token='fixture-token'))
    body = {'protocol_version': 2, 'binding_id': binding}
    headers = {'Authorization': 'Bearer fixture-token'}
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        mismatch = await client.post(f'/api/argus/internal/wakes/{uuid4()}/available', json=body, headers=headers)
        assert mismatch.status_code == 403 and mismatch.json()['protocol_version'] == 2
        await repository.finalize(binding, {'execution_stopped': True, 'exit_kind': 'completed', 'report_cursor': 0})
        closed = await client.post(f'/api/argus/internal/wakes/{wake.id}/available', json=body, headers=headers)
        assert closed.status_code == 409 and closed.json()['protocol_version'] == 2
        async def unavailable(*args): raise SQLAlchemyError('fixture unavailable')
        monkeypatch.setattr(service, 'available', unavailable)
        failed = await client.post(f'/api/argus/internal/wakes/{wake.id}/available', json=body, headers=headers)
        assert failed.status_code == 503 and failed.json()['protocol_version'] == 2


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
