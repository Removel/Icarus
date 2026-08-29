"""FastAPI application for the local Icarus Agent Gateway."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket

from apps.agent.src.application import AgentRuntime
from apps.gateway.src.connection import (
    ConnectionHub,
    GatewayConnection,
    RuntimeUpdatePump,
)
from apps.gateway.src.protocol.methods import GatewayMethods


def create_app(
    runtime: AgentRuntime | None = None,
    *,
    connection_queue_capacity: int = 4096,
) -> FastAPI:
    runtime = runtime or AgentRuntime()
    hub = ConnectionHub()
    methods = GatewayMethods(runtime)
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

    app = FastAPI(lifespan=lifespan)
    app.state.agent_runtime = runtime

    @app.get("/health")
    async def health():
        return {"status": "ready" if runtime.is_running else "stopped"}

    @app.websocket("/rpc")
    async def rpc(websocket: WebSocket):
        connection = GatewayConnection(
            websocket, methods, send_capacity=connection_queue_capacity
        )
        hub.add(connection)
        try:
            await connection.run()
        finally:
            hub.remove(connection)

    return app
