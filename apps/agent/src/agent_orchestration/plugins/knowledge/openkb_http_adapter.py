"""HTTP adapter translating the stable Knowledge contract to OpenKB."""

from __future__ import annotations

from typing import Any

import httpx

from apps.agent.src.agent_orchestration.plugins.knowledge.models import (
    KnowledgeCatalog,
    KnowledgeDocument,
    KnowledgePage,
    KnowledgeQueryResult,
    KnowledgeRecompileDocument,
    KnowledgeRecompileResult,
    KnowledgeUploadItem,
    KnowledgeUploadResult,
    KnowledgeUploadSource,
)


class KnowledgeBackendError(RuntimeError):
    pass


class OpenKBHttpAdapter:
    """Narrow OpenKB client with no delete, chat, or passthrough operation."""

    def __init__(
        self,
        endpoint: str,
        *,
        knowledge_base: str,
        api_token: str,
        client: httpx.Client | None = None,
    ) -> None:
        self.knowledge_base = knowledge_base
        headers = {"Authorization": f"Bearer {api_token}"} if api_token else {}
        self.client = client or httpx.Client(
            base_url=endpoint.rstrip("/"),
            headers=headers,
            timeout=httpx.Timeout(None, connect=10),
        )

    def query(self, question: str) -> KnowledgeQueryResult:
        payload = self._request(
            "POST", "/api/v1/query",
            json={
                "kb": self.knowledge_base,
                "question": question,
                "stream": False,
                "save": False,
            },
        )
        return KnowledgeQueryResult(answer=_required_text(payload, "answer"))

    def list(self) -> KnowledgeCatalog:
        payload = self._request(
            "POST", "/api/v1/list", json={"kb": self.knowledge_base}
        )
        documents = tuple(
            KnowledgeDocument(
                ref="knowledge-document:" + _required_text(item, "hash"),
                name=_required_text(item, "name"),
                document_type=str(item.get("type", "")),
                display_type=str(item.get("display_type", "")),
                pages=(item.get("pages") if isinstance(item.get("pages"), int) else None),
            )
            for item in _object_list(payload, "documents")
        )
        return KnowledgeCatalog(
            documents=documents,
            summaries=_page_refs(payload, "summaries"),
            concepts=_page_refs(payload, "concepts"),
            entities=_page_refs(payload, "entities"),
            reports=_page_refs(payload, "reports"),
        )

    def read(self, path: str) -> KnowledgePage:
        payload = self._request(
            "POST", "/api/v1/page",
            json={"kb": self.knowledge_base, "path": path},
        )
        return KnowledgePage(
            path=_required_text(payload, "path"),
            content=_required_text(payload, "content"),
        )

    def upload(
        self, sources: tuple[KnowledgeUploadSource, ...]
    ) -> KnowledgeUploadResult:
        files = [
            ("files", (source.name, source.stream, "application/octet-stream"))
            for source in sources
        ]
        payload = self._request(
            "POST", "/api/v1/add",
            data={"kb": self.knowledge_base, "stream": "false"},
            files=files,
        )
        items = tuple(
            KnowledgeUploadItem(
                name=_required_text(item, "original_name"),
                status=_required_text(item, "status"),
                message=str(item.get("message", "")),
            )
            for item in _object_list(payload, "files")
        )
        return KnowledgeUploadResult(
            files=items,
            added_count=_integer(payload, "added_count"),
            skipped_count=_integer(payload, "skipped_count"),
            failed_count=_integer(payload, "failed_count"),
        )

    def recompile(
        self,
        *,
        document: str | None,
        all_documents: bool,
        refresh_schema: bool,
    ) -> KnowledgeRecompileResult:
        try:
            response = self.client.request(
                "POST",
                "/api/v1/recompile",
                json={
                    "kb": self.knowledge_base,
                    "doc_name": document,
                    "all_docs": all_documents,
                    "dry_run": False,
                    "refresh_schema": refresh_schema,
                    "stream": False,
                },
            )
        except httpx.TimeoutException as error:
            raise KnowledgeBackendError("OpenKB request timed out") from error
        except httpx.HTTPError as error:
            raise KnowledgeBackendError("OpenKB service is unavailable") from error
        if response.status_code == 409:
            detail = _response_detail(response)
            if isinstance(detail, dict):
                candidates = tuple(
                    dict(item) for item in detail.get("candidates", [])
                    if isinstance(item, dict)
                )
                return KnowledgeRecompileResult(
                    status="ambiguous", total=0, recompiled=0, skipped=0,
                    candidates=candidates, message=str(detail.get("message", "")),
                )
        payload = self._response_payload(response)
        documents = tuple(
            KnowledgeRecompileDocument(
                name=_optional_text(item.get("name")),
                document=_optional_text(item.get("doc_name")),
                document_type=str(item.get("type", "")),
                status=_required_text(item, "status"),
                elapsed_seconds=(
                    float(item["elapsed"])
                    if isinstance(item.get("elapsed"), (int, float))
                    and not isinstance(item.get("elapsed"), bool)
                    else None
                ),
                message=_optional_text(item.get("message")),
            )
            for item in _object_list(payload, "docs")
        )
        return KnowledgeRecompileResult(
            status=_required_text(payload, "status"),
            total=_integer(payload, "total"),
            recompiled=_integer(payload, "recompiled"),
            skipped=_integer(payload, "skipped"),
            documents=documents,
            candidates=_candidates(payload.get("candidates")),
            message=_optional_text(payload.get("message")),
        )

    def close(self) -> None:
        self.client.close()

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        try:
            response = self.client.request(method, path, **kwargs)
        except httpx.TimeoutException as error:
            raise KnowledgeBackendError("OpenKB request timed out") from error
        except httpx.HTTPError as error:
            raise KnowledgeBackendError("OpenKB service is unavailable") from error
        return self._response_payload(response)

    @staticmethod
    def _response_payload(response: httpx.Response) -> dict[str, Any]:
        if response.is_error:
            detail = _response_detail(response)
            if response.status_code >= 500:
                message = "OpenKB service failed"
            elif isinstance(detail, str) and detail:
                message = detail[:500]
            elif isinstance(detail, dict):
                message = str(detail.get("message", "OpenKB request failed"))[:500]
            else:
                message = "OpenKB request failed"
            raise KnowledgeBackendError(f"{message} (HTTP {response.status_code})")
        try:
            payload = response.json()
        except ValueError as error:
            raise KnowledgeBackendError("OpenKB returned invalid JSON") from error
        if not isinstance(payload, dict):
            raise KnowledgeBackendError("OpenKB returned an invalid response object")
        return payload


def _response_detail(response: httpx.Response) -> object:
    try:
        payload = response.json()
    except ValueError:
        return None
    return payload.get("detail") if isinstance(payload, dict) else None


def _required_text(value: dict[str, Any], name: str) -> str:
    item = value.get(name)
    if not isinstance(item, str):
        raise KnowledgeBackendError(f"OpenKB response is missing {name}")
    return item


def _optional_text(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _integer(value: dict[str, Any], name: str) -> int:
    item = value.get(name)
    if isinstance(item, bool) or not isinstance(item, int):
        raise KnowledgeBackendError(f"OpenKB response is missing {name}")
    return item


def _object_list(value: dict[str, Any], name: str) -> tuple[dict[str, Any], ...]:
    items = value.get(name, [])
    if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
        raise KnowledgeBackendError(f"OpenKB response has invalid {name}")
    return tuple(items)


def _string_tuple(value: dict[str, Any], name: str) -> tuple[str, ...]:
    items = value.get(name, [])
    if not isinstance(items, list) or not all(isinstance(item, str) for item in items):
        raise KnowledgeBackendError(f"OpenKB response has invalid {name}")
    return tuple(items)


def _page_refs(value: dict[str, Any], section: str) -> tuple[str, ...]:
    return tuple(
        item if item.startswith(f"{section}/") else f"{section}/{item}"
        for item in _string_tuple(value, section)
    )


def _candidates(value: object) -> tuple[dict[str, str], ...]:
    if not isinstance(value, list):
        return ()
    return tuple(dict(item) for item in value if isinstance(item, dict))
