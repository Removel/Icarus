"""Directed calls cannot silently accept unsafe capacity or correlation reuse."""

import asyncio
import pytest
from icarus.core.capabilities import CapabilityRequester, ConversationReadRequest, CapabilityStatus
from icarus.runtime.manager import PluginManager


def test_directed_capabilities_reject_bounded_inbox_before_runtime_starts():
    manager = PluginManager(inbox_maxsize=1)
    with pytest.raises(ValueError, match="unbounded inbox"):
        manager.bind_capability("conversation.read", "provider", required_by={"caller"})


def test_timed_out_correlation_cannot_be_reused_by_later_request():
    async def run():
        async def send(provider, event):
            pass
        caller = CapabilityRequester(source_plugin_id="caller", resolve=lambda event: "provider", send=send)
        first = ConversationReadRequest(correlation_id="reused", timeout_seconds=0.001)
        assert (await caller.request(first)).status is CapabilityStatus.TIMEOUT
        with pytest.raises(ValueError, match="already used"):
            await caller.request(ConversationReadRequest(correlation_id="reused", timeout_seconds=0.001))
    asyncio.run(run())
