"""Correlation, timeout and cancellation are core contracts, not bus semantics."""

import asyncio
from importlib import import_module


def test_requester_pairs_only_expected_provider_and_task():
    async def run():
        api = import_module("icarus.core.capabilities")
        sent = asyncio.Event()
        captured = []
        async def send(provider, event):
            captured.append(event)
            sent.set()
        requester = api.CapabilityRequester(
            source_plugin_id="skill", resolve=lambda request: "board", send=send,
        )
        request = api.ConversationReadRequest(task_id="task")
        pending = asyncio.create_task(requester.request(request))
        await sent.wait()
        response = api.ConversationReadResponse(
            task_id="task", correlation_id=request.correlation_id,
            status=api.CapabilityStatus.OK,
        )
        assert not requester.accept_response("imposter", response)
        assert not pending.done()
        assert requester.accept_response("board", response)
        assert (await pending).status is api.CapabilityStatus.OK
        assert requester.pending_count == 0
        assert not requester.accept_response("board", response)
    asyncio.run(run())


def test_requester_timeout_and_close_release_pending():
    async def run():
        api = import_module("icarus.core.capabilities")
        sent = asyncio.Event()
        async def send(provider, event):
            sent.set()
        requester = api.CapabilityRequester(source_plugin_id="skill", resolve=lambda request: "board", send=send)
        response = await requester.request(api.ConversationReadRequest(timeout_seconds=0.001))
        assert response.status is api.CapabilityStatus.TIMEOUT
        assert requester.pending_count == 0
        sent.clear()
        pending = asyncio.create_task(requester.request(api.ConversationReadRequest()))
        await sent.wait()
        requester.close()
        assert (await pending).status is api.CapabilityStatus.UNAVAILABLE
        assert requester.pending_count == 0
    asyncio.run(run())
