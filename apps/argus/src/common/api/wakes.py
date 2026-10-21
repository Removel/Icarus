"""Durable request and simulated-result inspection with bounded pagination."""
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request

from ..repositories.observation_repository import NotFound
from ..schemas.wake import ACTIVE_STATUSES


def create_wake_router():
    router = APIRouter(prefix='/api/argus')

    def serialize(wake, repository):
        return {**wake.model_dump(mode='json'),
                'active': wake.boot_id == repository.boot_id and wake.status in ACTIVE_STATUSES}

    @router.get('/wake-requests')
    async def listing(request: Request, status: Literal['waiting', 'sent', 'accepted', 'resolved', 'no_action', 'failed'] | None = None,
                      chat_id: str | None = None, limit: int = Query(default=50, ge=1, le=100),
                      offset: int = Query(default=0, ge=0)):
        repository = request.app.state.repository
        return [serialize(wake, repository) for wake in await repository.list_wakes(
            status=status, chat_id=chat_id, limit=limit, offset=offset)]

    @router.get('/wake-requests/{wake_id}')
    async def detail(wake_id: UUID, request: Request):
        repository = request.app.state.repository
        try:
            wake = await repository.get_wake(wake_id)
            return {'wake': serialize(wake, repository), 'items': [item.model_dump(mode='json')
                    for item in await repository.wake_items(wake_id)]}
        except NotFound:
            raise HTTPException(404, 'Wake not found') from None

    return router
