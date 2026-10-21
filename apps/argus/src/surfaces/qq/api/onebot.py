"""Authenticated OneBot reports; streamed size enforcement precedes decoding."""

import hashlib
import hmac
import json
import re

from fastapi import APIRouter, HTTPException, Request, Response
from sqlalchemy.exc import SQLAlchemyError


def create_qq_router(config, *, secret):
    router = APIRouter(prefix='/api/argus/qq')

    @router.post('/onebot/events', status_code=204)
    async def receive(request: Request):
        if not getattr(request.app.state, 'accepting', False):
            raise HTTPException(503, 'Argus is not accepting reports')
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > config.max_body_bytes:
                raise HTTPException(413, 'Report is too large')
        signature = request.headers.get('X-Signature', '')
        expected = hmac.new(secret.encode(), body, hashlib.sha1).hexdigest()
        if not re.fullmatch(r'sha1=[0-9a-fA-F]{40}', signature) or not hmac.compare_digest(
                signature[5:].lower(), expected):
            raise HTTPException(401, 'Invalid report signature')
        if request.headers.get('X-Self-ID') != config.bot_id:
            raise HTTPException(403, 'Report account does not match')
        try:
            raw = json.loads(body)
            await request.app.state.qq_service.receive(raw)
        except (ValueError, TypeError, KeyError):
            raise HTTPException(422, 'Invalid OneBot report') from None
        except (SQLAlchemyError, OSError):
            raise HTTPException(503, 'Observation storage is unavailable') from None
        return Response(status_code=204)

    return router
