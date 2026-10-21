"""Read-only normalized observation access."""
from uuid import UUID
from fastapi import APIRouter, HTTPException, Query, Request
from ..repositories.observation_repository import NotFound


def create_observation_router():
    router = APIRouter(prefix='/api/argus')

    @router.get('/observed-items/{item_id}')
    async def item(item_id: UUID, request: Request):
        repository = request.app.state.repository
        try:
            return (await repository.get_item(item_id)).model_dump(mode='json')
        except NotFound:
            raise HTTPException(404, 'Observation not found') from None

    @router.get('/chats/{chat_id}/unhandled')
    async def unhandled(chat_id: str, request: Request, limit: int = Query(default=50, ge=1, le=100)):
        repository = request.app.state.repository
        return [item.model_dump(mode='json') for item in await repository.latest_open_items(chat_id, limit=limit)]

    return router
