import asyncio
import pytest
from apps.argus.test.application.conversation_fakes import make_flow, receive, drain


@pytest.mark.parametrize('private', [True, False], ids=['private','group'])
def test_continuation_resets_six_second_collection_window(tmp_path, private):
    async def exercise():
        flow = await make_flow(tmp_path)
        try:
            first = await receive(flow,text='我想请你做个工具',private=private)
            assert flow.scheduler.ready.empty()
            flow.clock.advance(4)
            second = await receive(flow,text='主要用来整理笔记',private=private)
            assert flow.scheduler.ready.empty()
            flow.clock.advance(5.99)
            await flow.scheduler.check_due()
            assert flow.scheduler.ready.empty()
            assert flow.judge.call_count == 0 and flow.gateway.submissions == []
            flow.clock.advance(0.01)
            await flow.scheduler.check_due()
            assert flow.scheduler.ready.qsize() == 1
            await drain(flow)
            first_status = await flow.store.status_details(first.id)
            second_status = await flow.store.status_details(second.id)
            assert first_status['batch_id'] == second_status['batch_id']
            assert first_status['status'] == second_status['status'] == 'submitted'
            assert len(flow.gateway.submissions) == 1
            prompt = flow.gateway.submissions[0]['prompt']
            assert '我想请你做个工具' in prompt and '主要用来整理笔记' in prompt
        finally:
            await flow.store.close()
    asyncio.run(exercise())
