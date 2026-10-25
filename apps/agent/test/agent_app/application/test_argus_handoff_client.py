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


def test_handoff_v2_operations_use_saved_version_and_pending_is_never_zero_on_failure():
    import json
    from apps.agent.test.agent_app.application.test_scoped_execution import request_v2, request, SESSION
    from agent_app.application.models.scoped_execution import ExecutionView
    from agent_app.application.services.argus_handoff_client import ArgusHandoffClient
    from agent_app.argus_protocol import HandoffError
    async def run():
        seen = []
        def respond(req):
            body = json.loads(req.content)
            seen.append((req.url.path, body))
            if req.url.path.endswith('/pending'):
                return httpx.Response(503, json={'protocol_version': 2, 'code': 'unavailable'})
            return httpx.Response(200, json={'protocol_version': body['protocol_version'], 'binding_id': BINDING})
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
            client = ArgusHandoffClient('http://argus.test', 'fixture-secret', http=http)
            value = request_v2()
            execution = ExecutionView(wake_id=value.wake_id, origin_boot_id=value.origin_boot_id,
                execution_id=BINDING, session_id=SESSION, task_id=BINDING, scope=value.scope)
            await client.bind(value, execution)
            await client.call(WAKE, 'available', {'binding_id': BINDING})
            await client.send_report(WAKE, BINDING, {'report_id': BINDING, 'items': []})
            await client.finalize(WAKE, BINDING, 'completed', 0)
            assert all(body['protocol_version'] == 2 for _, body in seen)
            with pytest.raises(HandoffError, match='unavailable'):
                await client.session_pending(SESSION, value.scope)
            assert seen[-1] == (f'/api/argus/internal/sessions/{SESSION}/pending',
                {'protocol_version': 2, 'scope': value.scope.model_dump(mode='json')})
            await client.bind(request(), execution)
            assert seen[-1][1]['protocol_version'] == 1
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
