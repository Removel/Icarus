"""Synchronous rela-mem REST adapter; no startup network requests."""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import quote

import httpx

from agent_app.plugins.relationship.backend import RelationshipBackendError
from agent_app.plugins.relationship.models import FindResult, PersonSummary, split_identity


_PERSON_FIELDS = ("person_id", "display_name", "kind", "status")
_RELATION_FIELDS = frozenset({"relation_id", "from_person_id", "to_person_id", "kind", "direction",
    "forward_detail", "reverse_detail", "scope_circle_id", "status", "source", "source_ref",
    "created_at", "updated_at", "relative_direction", "detail", "detail_missing"})


class RelaMemHttpAdapter:
    def __init__(self, endpoint: str, *, api_token: str = "", timeout_seconds: int = 10,
                 client: httpx.Client | None = None) -> None:
        self.client = client or httpx.Client(base_url=endpoint.rstrip("/"), timeout=timeout_seconds)
        self._headers = {"Accept": "application/json"}
        if api_token:
            self._headers["Authorization"] = f"Bearer {api_token}"
        self._timeout = timeout_seconds

    def __enter__(self) -> RelaMemHttpAdapter:
        return self

    def __exit__(self, *args) -> None:
        self.close()

    def close(self) -> None:
        self.client.close()

    def _request(self, method: str, path: str, *, max_bytes: int = 2 * 1024 * 1024,
                 **kwargs) -> dict[str, Any]:
        try:
            with self.client.stream(method, path, headers=self._headers, timeout=self._timeout,
                                    follow_redirects=False, **kwargs) as response:
                if not 200 <= response.status_code < 300:
                    raise RelationshipBackendError(f"service returned HTTP {response.status_code}",
                                                   status_code=response.status_code)
                if response.status_code == 204:
                    return {}
                data = bytearray()
                for chunk in response.iter_bytes():
                    if len(data) + len(chunk) > max_bytes:
                        raise RelationshipBackendError("service response exceeds safety limit")
                    data.extend(chunk)
                value = json.loads(data)
                if not isinstance(value, dict):
                    raise ValueError
                return value
        except httpx.HTTPError:
            raise RelationshipBackendError("service unavailable or request timed out") from None
        except (ValueError, UnicodeError, RecursionError):
            raise RelationshipBackendError("service returned invalid data") from None

    def find(self, query: str) -> FindResult:
        if split_identity(query):
            item = self.resolve_identity(query)
            return FindResult((item,) if item else ())
        return self.search_people(query)

    def search_people(self, name: str) -> FindResult:
        value = self._request("GET", "/v1/persons", params={"query": name, "limit": 10})
        items, more = _page(value)
        return FindResult(tuple(_person(item) for item in items[:10]), more or len(items) > 10)

    def resolve_identity(self, identity: str, *, include_archived: bool = False) -> PersonSummary | None:
        parsed = split_identity(identity)
        if parsed is None:
            raise ValueError("identity requires a non-name namespace prefix")
        params: dict[str, Any] = {"namespace": parsed[0], "value": parsed[1]}
        if include_archived:
            params["include_archived"] = True
        value = self._request("GET", "/v1/identity/resolve", params=params, max_bytes=1024 * 1024)
        if "person" not in value:
            raise RelationshipBackendError("service returned invalid person")
        return None if value["person"] is None else _person(value["person"])

    def get_person(self, person_id: str) -> PersonSummary:
        value = self._request("GET", f"/v1/persons/{_path(person_id)}", max_bytes=1024 * 1024)
        raw = value.get("person")
        if (not isinstance(raw, dict) or not isinstance(raw.get("aliases"), list)
                or raw.get("aliases_truncated", False) is not False):
            raise RelationshipBackendError("service returned incomplete person evidence")
        person = _person(raw)
        if person.person_id != person_id or person.status != "active":
            raise RelationshipBackendError("service returned inconsistent person identity")
        return person

    def context(self, person_id: str, query: str | None = None) -> dict[str, Any]:
        person = self.get_person(person_id).as_dict()
        root = f"/v1/persons/{_path(person_id)}"
        params: dict[str, Any] = {"limit": 20}
        if query is not None:
            params["q"] = query
        profile = self._request("GET", root + "/profile", params=params)
        circles = self._request("GET", root + "/circles", params={"limit": 20})
        relations = self._request("GET", "/v1/relations", params={"person_id": person_id,
                                  "include_incoming": True, "limit": 40})
        result: dict[str, Any] = {"person": person}
        for name, value, limit in (("profile", profile, 20), ("circles", circles, 20), ("relations", relations, 40)):
            rows, more = _page(value)
            items = [_project(name, row) for row in rows[:limit]]
            result[name] = {"items": items, "truncated": more or len(rows) > limit}
        # Drop complete entries rather than misrepresent a cropped JSON value as a fact.
        for name in ("profile", "circles", "relations"):
            while result[name]["items"] and len(json.dumps(result, ensure_ascii=False)) > 24000:
                result[name]["items"].pop()
                result[name]["truncated"] = True
        if len(json.dumps(result, ensure_ascii=False)) > 24000:
            raise RelationshipBackendError("person context exceeds safety limit")
        return result

    def create_person(self, display_name: str, identities: list[str]) -> PersonSummary:
        value = self._request("POST", "/v1/persons", json={"display_name": display_name,
                              "kind": "human", "aliases": [_alias(identity) for identity in identities]})
        return _person(value.get("person"))

    def add_alias(self, person_id: str, identity: str) -> None:
        self._request("POST", f"/v1/persons/{_path(person_id)}/aliases", json=_alias(identity))

    def write_profile(self, person_id: str, key: str, value: Any, source_ref: str) -> None:
        self._request("PUT", f"/v1/persons/{_path(person_id)}/profile/{_path(key)}",
                      json={"value": value, "source": "agent", "source_ref": source_ref})

    def forget_profile(self, person_id: str, key: str, source_ref: str) -> None:
        self._request("DELETE", f"/v1/persons/{_path(person_id)}/profile/{_path(key)}",
                      params={"source": "agent", "source_ref": source_ref})

    def upsert_relation(self, *, from_person_id: str, to_person_id: str, kind: str,
                        forward_detail: str, reverse_detail: str | None,
                        scope_circle_id: str | None, source_ref: str) -> dict[str, Any]:
        payload = {"from_person_id": from_person_id, "to_person_id": to_person_id, "kind": kind,
                   "forward_detail": forward_detail, "direction": "two_way" if reverse_detail else "one_way",
                   "source": "agent", "source_ref": source_ref}
        if reverse_detail:
            payload["reverse_detail"] = reverse_detail
        if scope_circle_id:
            payload["scope_circle_id"] = scope_circle_id
        value = self._request("POST", "/v1/relations", json=payload)
        relation = value.get("relation")
        if not isinstance(relation, dict) or not isinstance(relation.get("relation_id"), str) or type(value.get("created")) is not bool:
            raise RelationshipBackendError("service returned invalid relation")
        return {"relation": _project("relations", relation), "created": value["created"]}

    def invalidate_relation(self, relation_id: str) -> None:
        self._request("DELETE", f"/v1/relations/{_path(relation_id)}")


def _path(value: str) -> str:
    return quote(value, safe="")


def _alias(identity: str) -> dict[str, str]:
    parsed = split_identity(identity)
    if parsed is None:
        raise ValueError("identity requires a non-name namespace prefix")
    return {"namespace": parsed[0], "value": parsed[1]}


def _person(value: Any) -> PersonSummary:
    if not isinstance(value, dict) or any(not isinstance(value.get(key), str) or not value[key] for key in _PERSON_FIELDS):
        raise RelationshipBackendError("service returned invalid person")
    aliases = value.get("aliases", [])
    if not isinstance(aliases, list) or any(not isinstance(a, dict) or not isinstance(a.get("namespace"), str)
                                              or not isinstance(a.get("value"), str) for a in aliases):
        raise RelationshipBackendError("service returned invalid aliases")
    names = tuple(a["value"].strip().lower() for a in aliases if a["namespace"] == "name")
    safe = tuple({key: a[key] for key in ("alias_id", "namespace", "value", "display_value") if key in a} for a in aliases[:10])
    return PersonSummary(value["person_id"], value["display_name"], value["kind"], value["status"],
                         safe, len(aliases) > 10, names)


def _page(value: dict[str, Any]) -> tuple[list[dict[str, Any]], bool]:
    rows, more = value.get("items"), value.get("has_more")
    if not isinstance(rows, list) or type(more) is not bool or any(not isinstance(row, dict) for row in rows):
        raise RelationshipBackendError("service returned invalid page")
    return rows, more


def _project(name: str, row: dict[str, Any]) -> dict[str, Any]:
    allowed: set[str] | frozenset[str]
    if name == "profile":
        allowed = {"attr_key", "attr_value", "source", "source_ref", "recorded_at", "status"}
    elif name == "circles":
        allowed = {"circle_id", "display_name", "kind", "status", "role", "joined_at", "updated_at", "source", "source_ref"}
    else:
        allowed = _RELATION_FIELDS
    result = {key: value for key, value in row.items() if key in allowed}
    if name == "circles" and isinstance(row.get("circle"), dict):
        result["circle"] = {key: row["circle"][key] for key in
                            ("circle_id", "name", "kind", "status", "owner_person_id")
                            if key in row["circle"]}
    if name == "relations" and isinstance(row.get("other_person"), dict):
        result["other_person"] = {key: row["other_person"][key] for key in _PERSON_FIELDS if key in row["other_person"]}
    return result
