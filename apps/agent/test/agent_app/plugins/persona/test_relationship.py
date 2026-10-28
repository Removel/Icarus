import asyncio

import httpx
import pytest

from agent_app.plugins.persona.config import PersonaConfig
from agent_app.plugins.persona.relationship import RelationshipReader
from icarus.core.tools.result_budget import conservative_token_count


def reader(handler, **settings):
    config = PersonaConfig(relationship_enabled=True, **settings)
    client = httpx.AsyncClient(base_url=config.relationship_endpoint, transport=httpx.MockTransport(handler))
    return RelationshipReader(config, api_token="test-token", client=client)


def test_relationship_selects_user_and_bounds_complete_facts():
    async def run():
        calls = []
        def handler(request):
            calls.append(request)
            assert request.method == "GET"
            assert request.headers["Authorization"] == "Bearer test-token"
            if request.url.path == "/v1/persons":
                assert request.url.params["kind"] == "user"
                rows = [{"person_id": "p_user", "kind": "user", "status": "active", "display_name": "Friend"}]
            elif request.url.path.endswith("/profile"):
                rows = [{"attr_key": "likes", "attr_value": "tea"},
                        {"attr_key": "huge", "attr_value": "X" * 1000}]
            else:
                rows = [{"from_person_id": "icarus", "to_person_id": "p_user", "forward_detail": "伙伴",
                         "reverse_detail": "伙伴", "status": "active"}]
            return httpx.Response(200, json={"items": rows, "has_more": False})
        value = reader(handler)
        try:
            text = await value.load()
            assert "Friend" in text and "tea" in text
            assert "XXXX" not in text
            assert conservative_token_count(text) <= 200
            assert len(calls) == 3
        finally:
            await value.aclose()
    asyncio.run(run())


@pytest.mark.parametrize("rows,more", [([], False), ([{"person_id": "a"}, {"person_id": "b"}], False),
    ([{"person_id": "a", "kind": "user", "status": "active", "display_name": "U"}], True)])
def test_unknown_or_ambiguous_user_skips_context(rows, more, caplog):
    async def run():
        value = reader(lambda request: httpx.Response(200, json={"items": rows, "has_more": more}))
        try:
            assert await value.load() == ""
            assert caplog.records
        finally:
            await value.aclose()
    asyncio.run(run())


@pytest.mark.parametrize("mode", ["offline", "timeout", "malformed", "redirect"])
def test_dependency_failure_is_bounded_and_safe(mode, caplog):
    async def run():
        async def handler(request):
            if mode == "timeout":
                await asyncio.Event().wait()
            if mode == "offline":
                raise httpx.ConnectError("PRIVATE", request=request)
            if mode == "redirect":
                return httpx.Response(302, headers={"location": "https://other.example"})
            return httpx.Response(200, text="PRIVATE")
        value = reader(handler, relationship_timeout_seconds=0.03)
        try:
            assert await value.load() == ""
            assert "PRIVATE" not in caplog.text
            assert "test-token" not in caplog.text
        finally:
            await value.aclose()
    asyncio.run(run())


def test_failed_snapshot_cancels_and_awaits_sibling_request():
    async def run():
        sibling_started, sibling_finished = asyncio.Event(), asyncio.Event()
        async def handler(request):
            if request.url.path == "/v1/persons":
                return httpx.Response(200, json={"items": [{"person_id": "p_user", "kind": "user",
                    "status": "active", "display_name": "Friend"}], "has_more": False})
            if request.url.path.endswith("/profile"):
                await sibling_started.wait()
                return httpx.Response(503)
            sibling_started.set()
            try:
                await asyncio.Event().wait()
            finally:
                sibling_finished.set()
        value = reader(handler)
        try:
            assert await value.load() == ""
            assert sibling_finished.is_set()
        finally:
            await value.aclose()
    asyncio.run(run())


@pytest.mark.parametrize("endpoint,token,enabled", [("http://example.com", "token", True),
    ("https://example.com", "", True), ("http://localhost:8767", "token", False)])
def test_disabled_or_insecure_connection_makes_no_request(endpoint, token, enabled):
    async def run():
        def handler(request):
            pytest.fail("must not send request")
        config = PersonaConfig(relationship_enabled=enabled, relationship_endpoint=endpoint)
        value = RelationshipReader(config, api_token=token,
            client=httpx.AsyncClient(base_url=endpoint, transport=httpx.MockTransport(handler)))
        try:
            assert await value.load() == ""
        finally:
            await value.aclose()
    asyncio.run(run())
