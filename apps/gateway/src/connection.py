"""WebSocket connection state and device Update fan-out."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

from apps.agent.src.application import (
    RuntimeUpdateOverflowError,
    RuntimeUpdateSubscription,
)
from apps.agent.src.runtime_update import RuntimeUpdate
from apps.gateway.src.protocol.errors import GatewayRpcError, INTERNAL_ERROR
from apps.gateway.src.protocol.jsonrpc import (
    error_response,
    parse_request,
    success_response,
)
from apps.gateway.src.protocol.methods import GatewayMethods
from apps.gateway.src.protocol.models import (
    JsonRpcNotification,
    RuntimeUpdateModel,
)


_CLOSE = object()


class GatewayConnection:
    def __init__(
        self,
        websocket: WebSocket,
        methods: GatewayMethods,
        *,
        send_capacity: int = 4096,
    ) -> None:
        self.websocket = websocket
        self.methods = methods
        self.subscriptions: set[tuple[str, str]] = set()
        self._send_queue: asyncio.Queue[dict[str, Any] | object] = (
            asyncio.Queue(maxsize=send_capacity)
        )
        self._requests: set[asyncio.Task[None]] = set()
        self._closed = False

    async def run(self) -> None:
        await self.websocket.accept()
        sender = asyncio.create_task(self._send_loop())
        try:
            while True:
                text = await self.websocket.receive_text()
                task = asyncio.create_task(self._handle_text(text))
                self._requests.add(task)
                task.add_done_callback(self._requests.discard)
        except WebSocketDisconnect:
            pass
        finally:
            self._closed = True
            if self._requests:
                await asyncio.gather(*self._requests, return_exceptions=True)
            sender.cancel()
            await asyncio.gather(sender, return_exceptions=True)

    def accepts(self, update: RuntimeUpdate) -> bool:
        return (update.workspace_key, update.session_id) in self.subscriptions

    def offer_update(self, update: RuntimeUpdate) -> bool:
        notification = JsonRpcNotification(
            method="runtime.update",
            params=RuntimeUpdateModel.from_domain(update).model_dump(mode="json"),
        ).model_dump(mode="json")
        return self._offer(notification)

    async def close(self, code: int = 1013) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            await self.websocket.close(code=code)
        except Exception:
            pass

    async def _handle_text(self, text: str) -> None:
        request_id = None
        has_id = True
        try:
            request = parse_request(text)
            has_id = "id" in request.model_fields_set
            request_id = request.id if has_id else None
            result = await self.methods.dispatch(
                request.method, request.params, self.subscriptions
            )
            if has_id:
                self._offer(success_response(request.id, result))
        except GatewayRpcError as error:
            if has_id:
                self._offer(error_response(request_id, error))
        except Exception:
            if has_id:
                self._offer(
                    error_response(
                        request_id,
                        GatewayRpcError(INTERNAL_ERROR, "Internal error"),
                    )
                )

    def _offer(self, payload: dict[str, Any]) -> bool:
        if self._closed:
            return False
        try:
            self._send_queue.put_nowait(payload)
            return True
        except asyncio.QueueFull:
            asyncio.create_task(self.close())
            return False

    async def _send_loop(self) -> None:
        while True:
            payload = await self._send_queue.get()
            if payload is _CLOSE:
                return
            await self.websocket.send_text(
                json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            )


class ConnectionHub:
    def __init__(self) -> None:
        self._connections: set[GatewayConnection] = set()

    def add(self, connection: GatewayConnection) -> None:
        self._connections.add(connection)

    def remove(self, connection: GatewayConnection) -> None:
        self._connections.discard(connection)

    async def publish(self, update: RuntimeUpdate) -> None:
        slow = []
        for connection in tuple(self._connections):
            if connection.accepts(update) and not connection.offer_update(update):
                slow.append(connection)
        if slow:
            await asyncio.gather(
                *(connection.close() for connection in slow),
                return_exceptions=True,
            )

    async def close_all(self) -> None:
        connections = tuple(self._connections)
        self._connections.clear()
        if connections:
            await asyncio.gather(
                *(connection.close() for connection in connections),
                return_exceptions=True,
            )


class RuntimeUpdatePump:
    def __init__(
        self,
        runtime,
        hub: ConnectionHub,
        *,
        logger: logging.Logger | None = None,
    ) -> None:
        self.runtime = runtime
        self.hub = hub
        self.logger = logger or logging.getLogger("icarus.gateway.updates")
        self._task: asyncio.Task[None] | None = None
        self._subscription: RuntimeUpdateSubscription | None = None
        self._stopping = False

    def start(self) -> None:
        self._stopping = False
        self._subscription = self.runtime.subscribe_updates()
        self._task = asyncio.create_task(
            self._run(), name="gateway:runtime-updates"
        )

    async def stop(self) -> None:
        self._stopping = True
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
        subscription = self._subscription
        self._subscription = None
        if subscription is not None:
            subscription.close()

    async def _run(self) -> None:
        while not self._stopping:
            subscription = self._subscription
            if subscription is None:
                subscription = self.runtime.subscribe_updates()
                self._subscription = subscription
            try:
                while True:
                    await self.hub.publish(await subscription.next_update())
            except asyncio.CancelledError:
                raise
            except RuntimeUpdateOverflowError:
                self.logger.error("Gateway RuntimeUpdate subscription overflow")
                await self.hub.close_all()
            except RuntimeError:
                if not self._stopping:
                    self.logger.exception("Gateway RuntimeUpdate subscription failed")
                    await self.hub.close_all()
            finally:
                subscription.close()
                if self._subscription is subscription:
                    self._subscription = None
