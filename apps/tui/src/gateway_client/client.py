"""Async JSON-RPC client used by the Textual application."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from websockets.asyncio.client import connect

from packages.gateway_protocol import (
    ResourceRefModel,
    RuntimeUpdateModel,
)
from apps.tui.src.gateway_client.models import (
    SubmitAccepted,
    TaskOperationResult,
    UpdateSubscription,
)


class GatewayClientError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class GatewayClient:
    def __init__(
        self,
        *,
        url: str,
        workspace_path: str | Path,
        session_id: str | None = None,
        connector: Callable[[str], Awaitable[Any]] = connect,
    ) -> None:
        self.url = url
        self.workspace_path = str(Path(workspace_path).expanduser().resolve())
        self.session_id = session_id or uuid4().hex
        self.workspace_key: str | None = None
        self._connector = connector
        self._socket = None
        self._reader: asyncio.Task[None] | None = None
        self._send_lock = asyncio.Lock()
        self._pending: dict[str, asyncio.Future[Any]] = {}
        self._updates = UpdateSubscription()
        self._closed = False

    async def start(self) -> None:
        if self._socket is not None:
            return
        await self._connect()
        try:
            try:
                session = await self.request(
                    "session.get",
                    {
                        "workspace_path": self.workspace_path,
                        "session_id": self.session_id,
                    },
                )
            except GatewayClientError as error:
                if error.code != "session_not_found":
                    raise
                session = await self.request(
                    "session.create",
                    {
                        "workspace_path": self.workspace_path,
                        "session_id": self.session_id,
                    },
                )
            self.session_id = str(session["session_id"])
            self.workspace_key = str(session["workspace_key"])
            await self.request(
                "session.subscribe",
                {
                    "workspace_key": self.workspace_key,
                    "session_id": self.session_id,
                },
            )
        except BaseException:
            await self.close()
            raise

    async def reconnect(self) -> UpdateSubscription:
        if self._closed:
            raise RuntimeError("Gateway client is closed")
        await self._disconnect()
        self._updates = UpdateSubscription()
        await self._connect()
        session_id = self._require_session_id()
        session = await self.request(
            "session.get",
            {
                "workspace_path": self.workspace_path,
                "session_id": session_id,
            },
        )
        self.workspace_key = str(session["workspace_key"])
        await self.request(
            "session.subscribe",
            {
                "workspace_key": self.workspace_key,
                "session_id": session_id,
            },
        )
        return self._updates

    async def get_task_status(self, task_id: str) -> dict[str, Any]:
        return await self.request(
            "task.get_status",
            {
                "workspace_path": self.workspace_path,
                "session_id": self._require_session_id(),
                "task_id": task_id,
            },
        )

    def subscribe_updates(self) -> UpdateSubscription:
        if self._socket is None or self._closed:
            raise RuntimeError("Gateway client is not running")
        return self._updates

    async def submit(
        self,
        prompt: str,
        *,
        submission_id: str,
        resources: tuple[ResourceRefModel, ...] = (),
    ) -> SubmitAccepted:
        result = await self.request(
            "session.submit",
            {
                "workspace_path": self.workspace_path,
                "session_id": self._require_session_id(),
                "prompt": prompt,
                "submission_id": submission_id,
                "resources": [item.model_dump(mode="json") for item in resources],
            },
        )
        return SubmitAccepted(
            task_id=str(result["task_id"]),
            queue_position=int(result["queue_position"]),
        )

    async def cancel_task(
        self, task_id: str, reason: str | None = None
    ) -> TaskOperationResult:
        result = await self.request(
            "session.cancel",
            {
                "workspace_path": self.workspace_path,
                "session_id": self._require_session_id(),
                "task_id": task_id,
                "reason": reason,
            },
        )
        return TaskOperationResult(
            task_id=result.get("task_id"),
            status=str(result["status"]),
            run_id=result.get("run_id"),
        )

    async def request(self, method: str, params: dict[str, Any]) -> Any:
        socket = self._socket
        if socket is None or self._closed:
            raise RuntimeError("Gateway client is not running")
        request_id = uuid4().hex
        future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        payload = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params,
        }
        try:
            async with self._send_lock:
                await socket.send(
                    json.dumps(
                        payload, ensure_ascii=False, separators=(",", ":")
                    )
                )
            return await asyncio.shield(future)
        finally:
            self._pending.pop(request_id, None)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self._disconnect()
        error = RuntimeError("Gateway connection is closed")
        for future in tuple(self._pending.values()):
            if not future.done():
                future.set_exception(error)
        self._updates.close(error)

    async def _connect(self) -> None:
        self._socket = await self._connector(self.url)
        self._reader = asyncio.create_task(
            self._read_loop(), name="tui:gateway-reader"
        )

    async def _disconnect(self) -> None:
        socket = self._socket
        self._socket = None
        if socket is not None:
            await socket.close()
        reader = self._reader
        self._reader = None
        if reader is not None and reader is not asyncio.current_task():
            reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)

    async def _read_loop(self) -> None:
        try:
            while True:
                raw = await self._socket.recv()
                value = json.loads(raw)
                if value.get("method") == "runtime.update":
                    self._updates.publish(
                        RuntimeUpdateModel.model_validate(value["params"])
                    )
                    continue
                request_id = value.get("id")
                future = self._pending.get(str(request_id))
                if future is None or future.done():
                    continue
                if "error" in value:
                    error = value["error"]
                    data = error.get("data") or {}
                    future.set_exception(
                        GatewayClientError(
                            str(data.get("code", error.get("code"))),
                            str(error.get("message", "Gateway error")),
                        )
                    )
                else:
                    future.set_result(value.get("result"))
        except asyncio.CancelledError:
            raise
        except BaseException as error:
            if not self._closed:
                self._socket = None
                for future in tuple(self._pending.values()):
                    if not future.done():
                        future.set_exception(error)
                self._updates.close(error)

    def _require_session_id(self) -> str:
        if self.session_id is None:
            raise RuntimeError("Gateway Session is not initialized")
        return self.session_id
