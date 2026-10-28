"""Additional capability failure cases use real requester state."""

import asyncio
import pytest

from icarus.core.capabilities import CapabilityRequester, ConversationReadRequest, CapabilityStatus


def test_cancelled_caller_releases_pending_and_rejects_invalid_timeout():
    async def run():
        sent = asyncio.Event()
        async def send(provider, event):
            sent.set()
        requester = CapabilityRequester(source_plugin_id="skill", resolve=lambda event: "board", send=send)
        task = asyncio.create_task(requester.request(ConversationReadRequest()))
        await sent.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert requester.pending_count == 0
    asyncio.run(run())
    for timeout in (0, -1, float("nan"), float("inf"), True):
        with pytest.raises(ValueError):
            ConversationReadRequest(timeout_seconds=timeout)
