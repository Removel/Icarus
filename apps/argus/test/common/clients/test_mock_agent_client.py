from apps.argus.test.factories import Clock, payload_factory


async def test_mock_acceptance_is_not_completion_and_duplicates_are_safe():
    from apps.argus.src.common.clients.mock_agent_client import MockAgentClient
    clock, payload = Clock(), payload_factory()
    agent = MockAgentClient(completion_delay_seconds=0.05, clock=clock.now)
    assert (await agent.submit(payload)).accepted
    assert await agent.get_outcome(payload.wake_id) is None
    assert (await agent.submit(payload)).accepted
    clock.advance(0.05)
    result = await agent.get_outcome(payload.wake_id)
    assert result.status == 'resolved' and result.simulated
    assert result.item_ids == tuple(item.item_id for item in payload.items)
    assert await agent.get_outcome(payload.wake_id) == result


async def test_completed_mock_requests_release_full_payloads():
    from apps.argus.src.common.clients.mock_agent_client import MockAgentClient
    agent = MockAgentClient(completion_delay_seconds=0)
    for _ in range(1000):
        payload = payload_factory()
        await agent.submit(payload)
        assert await agent.get_outcome(payload.wake_id) is not None
        await agent.release(payload.wake_id)
    assert agent._pending == {}


async def test_same_wake_id_with_different_payload_rejected():
    from apps.argus.src.common.clients.mock_agent_client import MockAgentClient
    payload = payload_factory()
    agent = MockAgentClient()
    await agent.submit(payload)
    receipt = await agent.submit(payload.model_copy(update={'reason': 'different'}))
    assert not receipt.accepted and not receipt.retryable
