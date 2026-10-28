"""Correlation bookkeeping independent of transport, queues and plugin workers."""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from icarus.core.capabilities.base import CapabilityRequest, CapabilityResponse, CapabilityStatus


@dataclass
class _Pending:
    request: CapabilityRequest
    provider: str
    future: asyncio.Future[CapabilityResponse]


class CapabilityRequester:
    def __init__(
        self,
        *,
        source_plugin_id: str,
        resolve: Callable[[CapabilityRequest], str],
        send: Callable[[str, CapabilityRequest], Awaitable[None]],
        default_timeout: float = 30,
    ) -> None:
        self.source_plugin_id = source_plugin_id
        self._resolve = resolve
        self._send = send
        self.default_timeout = default_timeout
        self._pending: dict[str, _Pending] = {}
        self._used_correlations: set[str] = set()
        self._closed = False

    @property
    def pending_count(self) -> int:
        return len(self._pending)

    @staticmethod
    def failure(request: CapabilityRequest, status: CapabilityStatus) -> CapabilityResponse:
        return request.response_type(
            task_id=request.task_id, correlation_id=request.correlation_id,
            status=status, error_code=status.value,
        )

    async def request(self, event: CapabilityRequest) -> CapabilityResponse:
        if self._closed:
            return self.failure(event, CapabilityStatus.UNAVAILABLE)
        if event.correlation_id in self._used_correlations:
            raise ValueError("Correlation ID was already used by this requester")
        self._used_correlations.add(event.correlation_id)
        try:
            provider = self._resolve(event)
        except (KeyError, RuntimeError):
            return self.failure(event, CapabilityStatus.UNAVAILABLE)
        future = asyncio.get_running_loop().create_future()
        self._pending[event.correlation_id] = _Pending(event, provider, future)
        try:
            async with asyncio.timeout(event.timeout_seconds or self.default_timeout):
                try:
                    await self._send(provider, event)
                except (KeyError, RuntimeError):
                    return self.failure(event, CapabilityStatus.UNAVAILABLE)
                return await future
        except TimeoutError:
            return self.failure(event, CapabilityStatus.TIMEOUT)
        finally:
            self._pending.pop(event.correlation_id, None)
            if not future.done():
                future.cancel()

    def accept_response(self, source_plugin_id: str, event: CapabilityResponse) -> bool:
        pending = self._pending.get(event.correlation_id)
        if pending is None or pending.future.done():
            return False
        if source_plugin_id != pending.provider or type(event) is not pending.request.response_type:
            return False
        if event.task_id != pending.request.task_id:
            return False
        pending.future.set_result(event)
        return True

    def close(self) -> None:
        self._closed = True
        for pending in tuple(self._pending.values()):
            if not pending.future.done():
                pending.future.set_result(self.failure(pending.request, CapabilityStatus.UNAVAILABLE))
