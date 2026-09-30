"""Icarus Gateway JSON-RPC transport."""

import asyncio
from collections.abc import Awaitable, Callable
import json
from uuid import uuid4

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed


class GatewayTransportError(ConnectionError):
    pass


class GatewayBusinessError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class IcarusClient:
    def __init__(self, url: str, *, timeout_seconds: float = 30,
                 connector: Callable[..., Awaitable] = connect) -> None:
        self.url = url
        self.timeout_seconds = timeout_seconds
        self._connector = connector
        self._connect_lock = asyncio.Lock()
        self._socket = None
        self._reader: asyncio.Task | None = None
        self._pending: dict[str, asyncio.Future] = {}
        self._send_lock = asyncio.Lock()

    @property
    def connected(self) -> bool:
        return self._socket is not None and self._reader is not None and not self._reader.done()

    async def connect(self) -> None:
        async with self._connect_lock:
            if self.connected:
                return
            if self._socket is not None:
                await self._socket.close()
            self._socket = await self._connector(self.url, max_size=16 * 1024 * 1024)
            self._reader = asyncio.create_task(self._read(), name="argus:gateway-reader")

    async def close(self) -> None:
        if self._socket is not None:
            await self._socket.close()
            self._socket = None
        if self._reader is not None:
            self._reader.cancel()
            await asyncio.gather(self._reader, return_exceptions=True)
            self._reader = None

    async def _read(self) -> None:
        try:
            async for raw in self._socket:
                response = json.loads(raw)
                request_id = str(response.get("id"))
                future = self._pending.get(request_id)
                if future is None or future.done():
                    continue
                if "error" in response:
                    error = response["error"]
                    future.set_exception(GatewayBusinessError(
                        str((error.get("data") or {}).get("code", error.get("code"))),
                        str(error.get("message", "Gateway error")),
                    ))
                else:
                    future.set_result(response.get("result"))
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self._fail_pending(GatewayTransportError(str(error)))
        finally:
            self._fail_pending(GatewayTransportError("Gateway connection ended"))

    def _fail_pending(self, error: Exception) -> None:
        for future in self._pending.values():
            if not future.done():
                future.set_exception(error)

    async def call(self, method: str, params: dict) -> dict:
        if not self.connected:
            try:
                await self.connect()
            except (OSError, TimeoutError, ConnectionError) as error:
                raise GatewayTransportError(str(error)) from error
        request_id = uuid4().hex
        future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        try:
            async with self._send_lock:
                await self._socket.send(json.dumps({
                    "jsonrpc": "2.0", "id": request_id, "method": method, "params": params,
                }))
            return await asyncio.wait_for(asyncio.shield(future), timeout=self.timeout_seconds)
        except GatewayBusinessError:
            raise
        except (OSError, TimeoutError, ConnectionError, ConnectionClosed) as error:
            raise GatewayTransportError(str(error)) from error
        finally:
            self._pending.pop(request_id, None)

    async def session_get(self, workspace_path: str, session_id: str) -> dict:
        return await self.call("session.get", {"workspace_path": workspace_path, "session_id": session_id})

    async def session_create(self, workspace_path: str, session_id: str) -> dict:
        return await self.call("session.create", {"workspace_path": workspace_path, "session_id": session_id})

    async def session_submit(self, workspace_path: str, session_id: str,
                             prompt: str, submission_id: str) -> dict:
        return await self.call("session.submit", {
            "workspace_path": workspace_path, "session_id": session_id,
            "prompt": prompt, "submission_id": submission_id,
        })
