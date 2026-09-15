"""HTTP adapter from stable Icarus memory models to self-hosted Mem0."""

from __future__ import annotations

from typing import Any

import httpx

from apps.agent.src.agent_orchestration.plugins.memory.models import (
    MemoryHistoryItem,
    MemoryItem,
    MemoryRecallResult,
    MemoryRecord,
    MemoryScope,
)


class MemoryBackendError(RuntimeError):
    pass


class Mem0HttpAdapter:
    def __init__(
        self,
        endpoint: str,
        *,
        user_id: str,
        agent_id: str,
        api_key: str = "",
        preserve_input_language: bool = True,
        timeout_seconds: float = 30,
        client: httpx.Client | None = None,
        async_client: httpx.AsyncClient | None = None,
    ) -> None:
        endpoint = endpoint.strip().rstrip("/")
        if not endpoint or not user_id.strip() or not agent_id.strip():
            raise ValueError("Mem0 endpoint, user_id and agent_id are required")
        if not endpoint.startswith(("http://", "https://")):
            raise ValueError("Mem0 endpoint must use http or https")
        headers = {"Accept": "application/json"}
        if api_key:
            headers["X-API-Key"] = api_key
        self.endpoint = endpoint
        self.user_id = user_id.strip()
        self.agent_id = agent_id.strip()
        self.preserve_input_language = preserve_input_language
        self._headers = headers
        self._timeout_seconds = timeout_seconds
        self.client = client or httpx.Client(
            base_url=endpoint, headers=headers, timeout=timeout_seconds
        )
        self.async_client = async_client
        self._owns_client = client is None
        self._owns_async_client = async_client is None

    def recall(
        self,
        query: str,
        *,
        workspace_key: str,
        scope: MemoryScope | None,
        include_stopped: bool,
        top_k: int,
        threshold: float,
    ) -> MemoryRecallResult:
        payload = self._recall_payload(
            query, workspace_key=workspace_key, scope=scope,
            include_stopped=include_stopped, top_k=top_k, threshold=threshold,
        )
        value = self._request("POST", "/search", json=payload)
        return self._recall_result(value, query=query, workspace_key=workspace_key)

    async def arecall(
        self,
        query: str,
        *,
        workspace_key: str,
        scope: MemoryScope | None,
        include_stopped: bool,
        top_k: int,
        threshold: float,
    ) -> MemoryRecallResult:
        payload = self._recall_payload(
            query, workspace_key=workspace_key, scope=scope,
            include_stopped=include_stopped, top_k=top_k, threshold=threshold,
        )
        value = await self._arequest("POST", "/search", json=payload)
        return self._recall_result(value, query=query, workspace_key=workspace_key)

    def _recall_result(
        self, value: Any, *, query: str, workspace_key: str
    ) -> MemoryRecallResult:
        rows = value.get("results", value) if isinstance(value, dict) else value
        if not isinstance(rows, list):
            raise MemoryBackendError("memory recall failed: Mem0 returned invalid results")
        items = tuple(
            self._item(row, workspace_key=workspace_key)
            for row in rows
            if isinstance(row, dict)
        )
        return MemoryRecallResult(items=items, query=query)

    def get(self, ref: str) -> MemoryRecord:
        value = self._request("GET", f"/memories/{_memory_id(ref)}")
        if not isinstance(value, dict):
            raise MemoryBackendError("memory get failed: Mem0 returned invalid memory")
        return self._record(value)

    def history(self, ref: str) -> tuple[MemoryHistoryItem, ...]:
        value = self._request("GET", f"/memories/{_memory_id(ref)}/history")
        rows = value.get("results", value) if isinstance(value, dict) else value
        if not isinstance(rows, list):
            raise MemoryBackendError("memory history failed: Mem0 returned invalid history")
        return tuple(
            MemoryHistoryItem(
                operation=str(row.get("event", row.get("operation", "unknown"))),
                content=_optional_text(row.get("new_memory", row.get("memory"))),
                occurred_at=_optional_text(row.get("created_at", row.get("updated_at"))),
                raw_id=_optional_text(row.get("id")),
            )
            for row in rows
            if isinstance(row, dict)
        )

    def remember(
        self,
        content: str,
        *,
        workspace_key: str,
        scope: MemoryScope,
        metadata: dict[str, Any],
    ) -> tuple[MemoryRecord, ...]:
        run_id = _run_id(workspace_key, scope)
        payload = {
            "messages": [{"role": "user", "content": content}],
            "user_id": self.user_id,
            "agent_id": self.agent_id,
            "run_id": run_id,
            "metadata": metadata,
            "infer": True,
            "preserve_input_language": self.preserve_input_language,
        }
        value = self._request("POST", "/memories", json=payload)
        rows = value.get("results", []) if isinstance(value, dict) else []
        records = []
        for row in rows:
            if not isinstance(row, dict) or not row.get("id"):
                continue
            records.append(
                self._record(
                    {
                        **row,
                        "memory": row.get("memory", row.get("data", content)),
                        "user_id": row.get("user_id", self.user_id),
                        "agent_id": row.get("agent_id", self.agent_id),
                        "run_id": row.get("run_id", run_id),
                        "metadata": row.get("metadata", metadata),
                    }
                )
            )
        return tuple(records)

    def correct(self, ref: str, content: str) -> MemoryRecord:
        memory_id = _memory_id(ref)
        self._request("PUT", f"/memories/{memory_id}", json={"text": content})
        return self.get(ref)

    def set_expiration(self, ref: str, expiration_date: str | None) -> MemoryRecord:
        memory_id = _memory_id(ref)
        self._request(
            "PUT", f"/memories/{memory_id}",
            json={"expiration_date": expiration_date},
        )
        return self.get(ref)

    def delete(self, ref: str) -> None:
        self._request("DELETE", f"/memories/{_memory_id(ref)}")

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    async def aclose(self) -> None:
        if self._owns_async_client and self.async_client is not None:
            await self.async_client.aclose()
            self.async_client = None

    def _filters(
        self, workspace_key: str, scope: MemoryScope | None
    ) -> dict[str, Any]:
        result: dict[str, Any] = {
            "user_id": self.user_id,
            "agent_id": self.agent_id,
        }
        if scope is None:
            result["OR"] = [
                {"run_id": "global"},
                {"run_id": f"workspace:{workspace_key}"},
            ]
        else:
            result["run_id"] = _run_id(workspace_key, scope)
        return result

    def _recall_payload(
        self, query: str, *, workspace_key: str, scope: MemoryScope | None,
        include_stopped: bool, top_k: int, threshold: float,
    ) -> dict[str, Any]:
        return {
            "query": query,
            "filters": self._filters(workspace_key, scope),
            "top_k": top_k,
            "threshold": threshold,
            "show_expired": include_stopped,
        }

    def _record(self, value: dict[str, Any]) -> MemoryRecord:
        metadata = value.get("metadata")
        run_id = str(
            value.get("run_id")
            or (metadata.get("run_id") if isinstance(metadata, dict) else None)
            or ""
        )
        return MemoryRecord(
            item=self._item(value),
            user_id=str(value.get("user_id", "")),
            agent_id=str(value.get("agent_id", "")),
            run_id=run_id,
            expiration_date=_optional_text(value.get("expiration_date")),
            metadata=(
                value.get("metadata")
                if isinstance(value.get("metadata"), dict)
                else {}
            ),
        )

    def _item(
        self, value: dict[str, Any], *, workspace_key: str | None = None
    ) -> MemoryItem:
        memory_id = value.get("id")
        content = value.get("memory", value.get("data"))
        if not memory_id or not isinstance(content, str):
            raise MemoryBackendError("Mem0 memory is missing id or content")
        metadata = value.get("metadata")
        run_id = str(
            value.get("run_id")
            or (metadata.get("run_id") if isinstance(metadata, dict) else None)
            or "global"
        )
        scope: MemoryScope = "workspace" if run_id.startswith("workspace:") else "global"
        score = value.get("score")
        return MemoryItem(
            ref=f"memory:{memory_id}",
            content=content,
            relevance=(float(score) if isinstance(score, (int, float)) else None),
            scope=scope,
            created_at=_optional_text(value.get("created_at")),
            updated_at=_optional_text(value.get("updated_at")),
        )

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            response = self.client.request(method, path, **kwargs)
            response.raise_for_status()
            return response.json()
        except httpx.TimeoutException as error:
            raise MemoryBackendError("Mem0 request timed out") from error
        except httpx.HTTPStatusError as error:
            detail = _safe_detail(error.response)
            raise MemoryBackendError(
                f"Mem0 returned HTTP {error.response.status_code}: {detail}"
            ) from error
        except (httpx.HTTPError, ValueError) as error:
            raise MemoryBackendError("Mem0 service is unavailable or returned invalid JSON") from error

    async def _arequest(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            if self.async_client is None:
                self.async_client = httpx.AsyncClient(
                    base_url=self.endpoint,
                    headers=self._headers,
                    timeout=self._timeout_seconds,
                )
            response = await self.async_client.request(method, path, **kwargs)
            response.raise_for_status()
            return response.json()
        except httpx.TimeoutException as error:
            raise MemoryBackendError("Mem0 request timed out") from error
        except httpx.HTTPStatusError as error:
            detail = _safe_detail(error.response)
            raise MemoryBackendError(
                f"Mem0 returned HTTP {error.response.status_code}: {detail}"
            ) from error
        except (httpx.HTTPError, ValueError) as error:
            raise MemoryBackendError(
                "Mem0 service is unavailable or returned invalid JSON"
            ) from error


def stopped_date() -> str:
    return "1970-01-01"


def _run_id(workspace_key: str, scope: MemoryScope) -> str:
    return "global" if scope == "global" else f"workspace:{workspace_key}"


def _memory_id(ref: str) -> str:
    if not isinstance(ref, str) or not ref.startswith("memory:") or not ref[7:].strip():
        raise ValueError("ref must use memory:<id>")
    return ref[7:].strip()


def _optional_text(value: object) -> str | None:
    return str(value) if value is not None else None


def _safe_detail(response: httpx.Response) -> str:
    try:
        value = response.json()
    except ValueError:
        return "upstream request failed"
    if isinstance(value, dict) and isinstance(value.get("detail"), str):
        return value["detail"][:300]
    return "upstream request failed"
