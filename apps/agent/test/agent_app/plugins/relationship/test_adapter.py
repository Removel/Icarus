import json

import httpx
import pytest


def adapter(handler):
    from agent_app.plugins.relationship.rela_mem_http_adapter import RelaMemHttpAdapter
    return RelaMemHttpAdapter("http://localhost:8767", api_token="secret-token", client=httpx.Client(base_url="http://localhost:8767", transport=httpx.MockTransport(handler)))


def person(aliases=None):
    return {"person_id": "p_a", "display_name": "张三", "kind": "human", "status": "active", "aliases": aliases or []}


def test_find_name_calls_search_with_limit_10():
    def handle(request):
        assert request.url.path == "/v1/persons"
        assert dict(request.url.params) == {"query": "张三", "limit": "10"}
        assert request.headers["Authorization"] == "Bearer secret-token"
        return httpx.Response(200, json={"items": [person()], "has_more": False, "next_cursor": None})
    with adapter(handle) as backend:
        assert backend.find("张三").as_dict() == {"items": [person()], "ambiguous": False, "truncated": False}


def test_find_identity_projects_safe_bounded_aliases():
    aliases = [{"alias_id": f"a_{i}", "namespace": "name", "value": f"别名{i}", "identity_key": "secret", "normalized_value": "internal"} for i in range(12)]
    def handle(request):
        assert request.url.path == "/v1/identity/resolve"
        assert dict(request.url.params) == {"namespace": "qq", "value": "123"}
        return httpx.Response(200, json={"person": person(aliases)})
    with adapter(handle) as backend:
        result = backend.find("qq:123").as_dict()
        assert len(result["items"][0]["aliases"]) == 10
        assert result["items"][0]["aliases_truncated"] is True
        assert "identity_key" not in json.dumps(result)
        assert "normalized_value" not in json.dumps(result)


def test_resolve_missing_is_not_a_failure():
    with adapter(lambda request: httpx.Response(200, json={"person": None})) as backend:
        assert backend.find("qq:123").as_dict()["items"] == []


def test_full_name_evidence_is_kept_before_alias_projection():
    aliases = [{"namespace": "name", "value": f"名字{i}"} for i in range(11)]
    with adapter(lambda request: httpx.Response(200, json={"person": person(aliases)})) as backend:
        record = backend.get_person("p_a")
        assert "名字10" in record.names
        assert len(record.as_dict()["aliases"]) == 10


def test_context_composes_four_reads_and_maps_has_more():
    calls = []
    def handle(request):
        calls.append(request)
        path = request.url.path
        if path == "/v1/persons/p_a":
            return httpx.Response(200, json={"person": person()})
        if path.endswith("/profile"):
            assert dict(request.url.params) == {"q": "猫", "limit": "20"}
            return httpx.Response(200, json={"items": [{"attr_key": "likes", "attr_value": "猫", "source": "agent"}], "has_more": True})
        if path.endswith("/circles"):
            assert request.url.params["limit"] == "20"
        else:
            assert path == "/v1/relations"
            assert dict(request.url.params) == {"person_id": "p_a", "include_incoming": "true", "limit": "40"}
        return httpx.Response(200, json={"items": [], "has_more": False})
    with adapter(handle) as backend:
        output = backend.context("p_a", "猫")
        assert len(calls) == 4
        assert output["profile"]["truncated"] is True
        assert output["relations"] == {"items": [], "truncated": False}
        assert output["profile"]["items"][0]["attr_value"] == "猫"


def test_context_omits_whole_oversized_values():
    def handle(request):
        if request.url.path == "/v1/persons/p_a":
            return httpx.Response(200, json={"person": person()})
        return httpx.Response(200, json={"items": [{"attr_key": "huge", "attr_value": "x" * 65000}], "has_more": False})
    with adapter(handle) as backend:
        result = backend.context("p_a")
        assert len(json.dumps(result, ensure_ascii=False)) <= 24000
        assert result["profile"]["truncated"] is True
        assert result["profile"]["items"] == []


def test_write_paths_source_and_empty_delete_responses():
    calls = []
    def handle(request):
        calls.append(request)
        if request.method == "DELETE":
            return httpx.Response(204)
        if request.url.path.endswith("/relations"):
            return httpx.Response(200, json={"relation": {"relation_id": "r_1", "direction": "one_way"}, "created": True})
        return httpx.Response(200, json={"entry": {}})
    with adapter(handle) as backend:
        backend.write_profile("p_a", "likes", None, "agent:task:run")
        backend.forget_profile("p_a", "likes", "agent:task:run")
        backend.upsert_relation(from_person_id="p_a", to_person_id="p_b", kind="friend", forward_detail="朋友", reverse_detail=None, scope_circle_id=None, source_ref="agent:task:run")
        backend.invalidate_relation("r_1")
    assert json.loads(calls[0].content) == {"value": None, "source": "agent", "source_ref": "agent:task:run"}
    assert dict(calls[1].url.params) == {"source": "agent", "source_ref": "agent:task:run"}
    relation = json.loads(calls[2].content)
    assert relation["direction"] == "one_way"
    assert "reverse_detail" not in relation
    assert relation["source"] == "agent"
    assert calls[3].url.path == "/v1/relations/r_1"


def test_create_includes_initial_identities_in_one_request():
    def handle(request):
        assert request.url.path == "/v1/persons"
        assert json.loads(request.content) == {"display_name": "张三", "kind": "human", "aliases": [{"namespace": "qq", "value": "123"}]}
        return httpx.Response(201, json={"person": person(), "created": True})
    with adapter(handle) as backend:
        assert backend.create_person("张三", ["qq:123"]).person_id == "p_a"


@pytest.mark.parametrize("status", [401, 404, 409, 500])
def test_http_failure_never_exposes_body_or_token_and_does_not_retry(status):
    from agent_app.plugins.relationship.backend import RelationshipBackendError
    calls = []
    def handle(request):
        calls.append(request)
        return httpx.Response(status, text="secret-token private server stack")
    with adapter(handle) as backend:
        with pytest.raises(RelationshipBackendError) as failure:
            backend.write_profile("p_a", "likes", "private-value", "agent:t:r")
        assert failure.value.status_code == status
        assert "secret-token" not in str(failure.value)
        assert "private" not in str(failure.value)
        assert len(calls) == 1


@pytest.mark.parametrize("response", [httpx.Response(200, text="not json"), httpx.Response(200, json={"items": [], "has_more": "false"}), httpx.Response(200, json={"items": [{}], "has_more": False})])
def test_malformed_results_are_not_silent_empty_success(response):
    from agent_app.plugins.relationship.backend import RelationshipBackendError
    with adapter(lambda request: response) as backend:
        with pytest.raises(RelationshipBackendError):
            backend.find("张三")


def test_detail_response_limit_and_client_close():
    from agent_app.plugins.relationship.backend import RelationshipBackendError
    backend = adapter(lambda request: httpx.Response(200, content=b"x" * (1024 * 1024 + 1)))
    with pytest.raises(RelationshipBackendError):
        backend.get_person("p_a")
    backend.close()
    backend.close()
    assert backend.client.is_closed
