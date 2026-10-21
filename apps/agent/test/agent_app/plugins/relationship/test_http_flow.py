"""HTTP contract spanning real tools and adapter with deterministic service fixtures."""

import json

import httpx

from agent_app.plugins.relationship.plugin import RelationshipPlugin
from agent_app.plugins.relationship.rela_mem_http_adapter import RelaMemHttpAdapter


def test_five_tools_complete_http_flow_and_preserve_circle_and_relation_view():
    profile = {}
    relations = []
    writes = []
    person = {"person_id": "p_a", "display_name": "张三", "kind": "human", "status": "active", "aliases": []}
    def handle(request):
        path = request.url.path
        if request.method in {"PUT", "POST", "DELETE"}:
            writes.append((request.method, path))
        if path == "/v1/identity/resolve":
            return httpx.Response(200, json={"person": person})
        if path == "/v1/persons/p_a":
            return httpx.Response(200, json={"person": person})
        if path == "/v1/persons":
            return httpx.Response(200, json={"items": [person], "has_more": False})
        if path == "/v1/persons/p_a/profile/likes":
            if request.method == "PUT":
                body = json.loads(request.content)
                assert body["source_ref"] == "agent:task:run"
                profile["likes"] = body["value"]
                return httpx.Response(200, json={"entry": {"attr_key": "likes", "attr_value": body["value"]}})
            profile.pop("likes", None)
            return httpx.Response(204)
        if path.endswith("/profile"):
            return httpx.Response(200, json={"items": [{"attr_key": k, "attr_value": v} for k, v in profile.items()], "has_more": False})
        if path.endswith("/circles"):
            return httpx.Response(200, json={"items": [{"circle_id": "c_1", "role": "member", "status": "active", "circle": {"circle_id": "c_1", "name": "摄影社", "kind": "hobby", "status": "active", "owner_person_id": "p_a", "internal": "hidden"}}], "has_more": False})
        if path == "/v1/relations" and request.method == "POST":
            body = json.loads(request.content)
            relations.append({"relation_id": "r_1", **body})
            return httpx.Response(200, json={"relation": relations[0], "created": True})
        if path == "/v1/relations":
            return httpx.Response(200, json={"items": relations, "has_more": False})
        if path == "/v1/relations/r_1":
            relations.clear()
            return httpx.Response(204)
        raise AssertionError((request.method, path))
    backend = RelaMemHttpAdapter("http://localhost", client=httpx.Client(base_url="http://localhost", transport=httpx.MockTransport(handle)))
    plugin = RelationshipPlugin("relationship", backend)
    tools = {tool.definition.name: tool for tool in plugin.build_tools()}
    try:
        assert tools["relationship_find"].invoke({"query": "张三"}).success
        assert tools["relationship_remember"].invoke({"person_id": "p_a", "profile": {"likes": "摄影"}}, task_id="task", run_id="run").success
        assert tools["relationship_relate"].invoke({"from_person_id": "p_a", "to_person_id": "p_b", "kind": "friend", "forward_detail": "朋友", "reverse_detail": "朋友"}).success
        result = tools["relationship_context"].invoke({"person_id": "p_a"})
        assert result.success
        assert result.output["profile"]["items"][0]["attr_value"] == "摄影"
        assert result.output["circles"]["items"][0]["circle"]["name"] == "摄影社"
        assert "internal" not in json.dumps(result.output)
        assert result.output["relations"]["items"][0]["direction"] == "two_way"
        assert tools["relationship_forget"].invoke({"person_id": "p_a", "keys": ["likes"]}).success
        assert tools["relationship_relate"].invoke({"relation_id": "r_1"}).success
        assert profile == {} and relations == []
        assert writes == [("PUT", "/v1/persons/p_a/profile/likes"), ("POST", "/v1/relations"), ("DELETE", "/v1/persons/p_a/profile/likes"), ("DELETE", "/v1/relations/r_1")]
    finally:
        backend.close()
