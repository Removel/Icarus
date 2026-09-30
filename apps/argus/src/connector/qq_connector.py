"""Authenticated SnowLuma OneBot HTTP ingress."""

import asyncio
import hashlib
import hmac
import json
import re

from fastapi import APIRouter, HTTPException, Request, Response

from apps.argus.src.config.config_model import QQConfig
from apps.argus.src.dao.observation_store import ObservationConflictError, ObservationStore
from apps.argus.src.handler.qq_handler import QQHandler
from apps.argus.src.queue import InboundQueue


class QQConnector:
    def __init__(self, config: QQConfig, secret: str, store: ObservationStore,
                 queue: InboundQueue | None = None) -> None:
        if not config.enabled or not secret:
            raise ValueError("QQ connector requires enabled settings and report secret")
        self.handler = QQHandler(config.bot_id)
        self.queue = queue
        self.router = APIRouter()

        @self.router.post("/onebot/events", status_code=204)
        async def onebot_event(request: Request) -> Response:
            if not getattr(request.app.state, "accepting", True):
                raise HTTPException(status_code=503, detail="Argus is stopping")
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > config.max_body_bytes:
                    raise HTTPException(status_code=413, detail="OneBot request too large")
            signature = request.headers.get("X-Signature", "")
            if not re.fullmatch(r"sha1=[a-fA-F0-9]{40}", signature):
                raise HTTPException(status_code=401, detail="Invalid OneBot signature")
            expected = "sha1=" + hmac.new(secret.encode(), body, hashlib.sha1).hexdigest()
            if not hmac.compare_digest(signature.lower(), expected):
                raise HTTPException(status_code=401, detail="Invalid OneBot signature")
            if request.headers.get("X-Self-ID") != config.bot_id:
                raise HTTPException(status_code=403, detail="Wrong OneBot account")
            try:
                payload = json.loads(body)
                observation = self.handler.parse(payload)
            except (ValueError, TypeError) as error:
                raise HTTPException(status_code=422, detail="Invalid OneBot message") from error
            try:
                inserted = await store.insert(observation)
            except ObservationConflictError as error:
                raise HTTPException(status_code=409, detail="Conflicting source message") from error
            if inserted:
                queue = self.queue or getattr(request.app.state, "queue", None)
                if queue is not None:
                    try:
                        queue.put_nowait(observation)
                    except asyncio.QueueFull:
                        pass  # Committed pending observations are replenished by the recovery loop.
            return Response(status_code=204)
