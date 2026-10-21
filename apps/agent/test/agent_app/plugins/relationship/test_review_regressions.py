import httpx

from agent_app.plugins.relationship.plugin import RelationshipPlugin
from agent_app.plugins.relationship.rela_mem_http_adapter import RelaMemHttpAdapter


def test_colon_display_name_always_uses_name_search_before_new_identity_creation():
    writes = []
    person = {"person_id": "p_a", "display_name": "alice:work", "kind": "human", "status": "active", "aliases": []}
    def handle(request):
        if request.method != "GET":
            writes.append(request.url.path)
            return httpx.Response(201, json={"person": {**person, "person_id": "p_new"}, "created": True})
        if request.url.path == "/v1/persons":
            assert request.url.params["query"] == "alice:work"
            return httpx.Response(200, json={"items": [person], "has_more": False})
        return httpx.Response(200, json={"person": None})
    backend = RelaMemHttpAdapter("http://localhost", client=httpx.Client(base_url="http://localhost", transport=httpx.MockTransport(handle)))
    try:
        tool = RelationshipPlugin("relationship", backend).build_tools()[2]
        result = tool.invoke({"display_name": "alice:work", "identities": ["qq:123"], "profile": {"likes": "cat"}})
        assert result.success and result.output["status"] == "confirmation_required"
        assert writes == []
    finally:
        backend.close()


def test_get_person_wrong_id_cannot_redirect_explicit_person_write():
    writes = []
    other = {"person_id": "p_b", "display_name": "B", "kind": "human", "status": "active", "aliases": []}
    def handle(request):
        if request.method != "GET":
            writes.append(request.url.path)
            return httpx.Response(200, json={})
        return httpx.Response(200, json={"person": other})
    backend = RelaMemHttpAdapter("http://localhost", client=httpx.Client(base_url="http://localhost", transport=httpx.MockTransport(handle)))
    try:
        tool = RelationshipPlugin("relationship", backend).build_tools()[2]
        result = tool.invoke({"person_id": "p_a", "identities": ["qq:123"], "profile": {"likes": "cat"}})
        assert result.success is False
        assert writes == []
    finally:
        backend.close()


def test_missing_full_alias_evidence_cannot_select_unique_display_name():
    writes = []
    first = {"person_id": "p_a", "display_name": "张三", "kind": "human", "status": "active", "aliases": []}
    second = {"person_id": "p_b", "display_name": "张三丰", "kind": "human", "status": "active"}
    def handle(request):
        if request.method != "GET":
            writes.append(request.url.path)
            return httpx.Response(200, json={})
        if request.url.path == "/v1/persons":
            return httpx.Response(200, json={"items": [first, {**second, "aliases": []}], "has_more": False})
        return httpx.Response(200, json={"person": first if request.url.path.endswith("p_a") else second})
    backend = RelaMemHttpAdapter("http://localhost", client=httpx.Client(base_url="http://localhost", transport=httpx.MockTransport(handle)))
    try:
        result = RelationshipPlugin("relationship", backend).build_tools()[2].invoke({"display_name": "张三", "profile": {"likes": "cat"}})
        assert result.success is False
        assert writes == []
    finally:
        backend.close()
