"""One shared readiness implementation for lifecycle and application probes."""
from fastapi import APIRouter, HTTPException, Request


def create_health_router(database):
    router = APIRouter()

    async def health(request: Request):
        task = getattr(request.app.state, 'scheduler_task', None)
        if (not getattr(request.app.state, 'ready', False) or (task is not None and task.done())
                or not await database.is_available()):
            raise HTTPException(503, 'Argus is not ready')
        return {'status': 'ready'}

    router.add_api_route('/health', health, methods=['GET'])
    router.add_api_route('/api/argus/health', health, methods=['GET'])
    return router
