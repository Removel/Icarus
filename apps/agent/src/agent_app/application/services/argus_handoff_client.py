"""Application-owned authenticated Argus HTTP boundary, without plugin dependencies."""

import httpx

from agent_app.argus_protocol import HandoffError, canonical_json


class ArgusHandoffClient:
    def __init__(self, base_url: str, token: str, *, http: httpx.AsyncClient | None = None):
        if not token or not base_url.startswith(("http://", "https://")):
            raise ValueError("Argus service configuration is required")
        self.base_url = base_url.rstrip("/")
        self._token = token
        self._owns_http = http is None
        self.http = http or httpx.AsyncClient(timeout=35)

    async def call(self, wake_id: str, operation: str, payload: dict) -> dict:
        if operation not in {"bind", "deliver", "read", "history", "wait", "report", "finalize"}:
            raise ValueError("Unknown Argus operation")
        body = canonical_json({**payload, "protocol_version": 1}).encode("utf-8")
        if len(body) > 1048576:
            raise HandoffError("payload_too_large")
        try:
            async with self.http.stream(
                "POST", f"{self.base_url}/api/argus/internal/wakes/{wake_id}/{operation}",
                content=body, headers={"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"},
            ) as response:
                chunks, size = [], 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > 1048576:
                        raise HandoffError("response_too_large")
                    chunks.append(chunk)
                import json
                try:
                    value = json.loads(b"".join(chunks))
                except (ValueError, UnicodeError):
                    raise HandoffError("unavailable", retryable=True) from None
                if not isinstance(value, dict) or value.get("protocol_version") != 1:
                    raise HandoffError("protocol_error")
                if response.status_code == 409 and operation == "report" and value.get("disposition") == "rejected":
                    if value.get("code") in {"item_not_reportable", "content_restricted", "result_conflict"}:
                        return value
                if response.is_error:
                    code = value.get("code")
                    safe_codes = {"content_restricted", "not_found", "unauthorized", "scope_mismatch", "conflict",
                                  "report_id_conflict", "execution_closed", "item_not_reportable", "result_conflict"}
                    raise HandoffError(code if code in safe_codes else "unavailable", retryable=response.status_code >= 500)
                return value
        except httpx.HTTPError:
            raise HandoffError("unavailable", retryable=True) from None

    async def bind(self, request, execution) -> dict:
        return await self.call(request.wake_id, "bind", {
            "execution_id": execution.execution_id, "session_id": execution.session_id,
            "task_id": execution.task_id, "scope": request.scope.model_dump(mode="json"),
            "origin_boot_id": request.origin_boot_id, "request_fingerprint": request.fingerprint,
        })

    async def send_report(self, wake_id, binding_id, report) -> dict:
        return await self.call(wake_id, "report", {"binding_id": binding_id, **report})

    async def finalize(self, wake_id, binding_id, exit_kind, report_cursor) -> dict:
        return await self.call(wake_id, "finalize", {
            "binding_id": binding_id, "execution_stopped": True,
            "exit_kind": exit_kind, "report_cursor": report_cursor,
        })

    async def aclose(self):
        if self._owns_http:
            await self.http.aclose()
