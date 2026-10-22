"""FastAPI application for the local Icarus Agent Gateway."""

from __future__ import annotations

from icarus.runtime.fanout import DEFAULT_QUEUE_CAPACITY

from contextlib import asynccontextmanager
from pathlib import Path
import os

from fastapi import FastAPI, WebSocket

from agent_app.application import AgentRuntime
from apps.gateway.src.connection import (
    ConnectionHub,
    GatewayConnection,
    RuntimeUpdatePump,
)
from apps.gateway.src.protocol.methods import GatewayMethods
from apps.gateway.src.protocol.argus_methods import ArgusMethods
from apps.gateway.src.resources import image_routes


def create_app(
    runtime: AgentRuntime | None = None,
    *,
    connection_queue_capacity: int | None = None,
    resource_data_dir: str | Path | None = None,
    argus_token_loader=lambda: os.getenv('ARGUS_GATEWAY_TOKEN'),
    service_only: bool = False,
    shared_app: FastAPI | None = None,
) -> FastAPI:
    runtime = shared_app.state.agent_runtime if shared_app is not None else (runtime or AgentRuntime())
    hub = shared_app.state.connection_hub if shared_app is not None else ConnectionHub()
    methods = GatewayMethods(runtime)
    argus_methods = ArgusMethods(runtime, token_loader=argus_token_loader)
    pump = RuntimeUpdatePump(runtime, hub)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        del app
        await runtime.start()
        pump.start()
        try:
            yield
        finally:
            await hub.close_all()
            await pump.stop()
            await runtime.stop()

    app = FastAPI(lifespan=lifespan if shared_app is None else None)
    app.state.agent_runtime = runtime
    app.include_router(image_routes(runtime, data_dir=resource_data_dir))
    app.state.connection_hub = hub

    @app.get("/health")
    async def health():
        return {"status": "ready" if runtime.is_running else "stopped"}

    async def serve_rpc(websocket: WebSocket, dispatcher):
        connection = GatewayConnection(
            websocket, dispatcher, send_capacity=(connection_queue_capacity
                if connection_queue_capacity is not None
                else getattr(runtime, "update_queue_capacity", DEFAULT_QUEUE_CAPACITY))
        )
        hub.add(connection)
        try:
            await connection.run()
        finally:
            hub.remove(connection)

    if not service_only:
        @app.websocket('/rpc')
        async def rpc(websocket: WebSocket):
            await serve_rpc(websocket, methods)

    @app.websocket('/argus/rpc')
    async def argus_rpc(websocket: WebSocket):
        await serve_rpc(websocket, argus_methods)

    return app
