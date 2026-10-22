"""HTTP identity, bounded errors and definitive report rejection."""

import asyncio

import httpx
import pytest


WAKE = "10000000-0000-4000-8000-000000000001"
BINDING = "70000000-0000-4000-8000-000000000001"


def test_handoff_http_client_keeps_token_out_of_business_payload():
    async def run():
        from agent_app.application.services.argus_handoff_client import ArgusHandoffClient
        seen = []

        def respond(request):
            seen.append(request)
            return httpx.Response(200, json={"protocol_version": 1, "binding_id": BINDING})

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
            client = ArgusHandoffClient("http://argus.test", "fixture-secret", http=http)
            response = await client.call(WAKE, "bind", {"execution_id": "execution"})
            assert response["binding_id"] == BINDING
            assert seen[0].url.path == f"/api/argus/internal/wakes/{WAKE}/bind"
            assert seen[0].headers["authorization"] == "Bearer fixture-secret"
            assert b'"protocol_version":1' in seen[0].content
            assert b"fixture-secret" not in seen[0].content

    asyncio.run(run())


def test_handoff_client_distinguishes_rejected_report_from_unknown_failure():
    async def run():
        from agent_app.application.services.argus_handoff_client import ArgusHandoffClient
        from agent_app.argus_protocol import HandoffError

        def respond(request):
            if request.url.path.endswith("/report"):
                return httpx.Response(409, json={"protocol_version": 1, "report_id": "report",
                    "disposition": "rejected", "code": "item_not_reportable", "report_cursor": 0})
            return httpx.Response(503, text="private database connection details")

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
            client = ArgusHandoffClient("http://argus.test", "fixture-secret", http=http)
            rejected = await client.call(WAKE, "report", {"binding_id": BINDING, "report_id": "report", "items": []})
            assert rejected["disposition"] == "rejected"
            with pytest.raises(HandoffError) as error:
                await client.call(WAKE, "finalize", {"binding_id": BINDING})
            assert error.value.retryable is True
            assert "private" not in str(error.value)
            assert "fixture-secret" not in str(error.value)

    asyncio.run(run())
