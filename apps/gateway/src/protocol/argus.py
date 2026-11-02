"""Protected, non-RPC bridge endpoints. Public RPC has no bridge methods."""
import hmac
import os
from fastapi import APIRouter, Depends, Header, HTTPException
from agent_app.argus_protocol import SessionRequest, WakeRequest, StatusRequest
from agent_app.application.services.argus_bridge import ArgusBridge, BridgeConflict


def argus_router(runtime):
    router = APIRouter(prefix='/argus')
    bridge = ArgusBridge(runtime)
    router.argus_bridge = bridge

    def authorized(authorization: str | None = Header(default=None)):
        token = os.getenv('ARGUS_GATEWAY_TOKEN', '')
        if not token or not authorization or not hmac.compare_digest(authorization, 'Bearer ' + token):
            raise HTTPException(401, 'Argus bearer authentication required')

    @router.post('/session', dependencies=[Depends(authorized)])
    async def session(request: SessionRequest):
        return await bridge.ensure_session(request.channel_id, request.request_id)

    @router.post('/wake', dependencies=[Depends(authorized)])
    async def wake(request: WakeRequest):
        try:
            result = await bridge.wake(request)
            return {'task_id':result['task_id'],'status':result['status']}
        except BridgeConflict as error:
            raise HTTPException(409, str(error)) from error

    @router.post('/status', dependencies=[Depends(authorized)])
    async def status(request: StatusRequest):
        try:
            return await bridge.status(request.wake_id)
        except KeyError as error:
            raise HTTPException(404, 'Unknown wake') from error
    return router
