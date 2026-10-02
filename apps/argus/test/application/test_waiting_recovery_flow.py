import asyncio
from apps.argus.test.application.conversation_fakes import make_flow, receive, drain


def test_waiting_rechecks_after_completion_without_another_message(tmp_path):
    async def exercise():
        flow = await make_flow(tmp_path)
        try:
            flow.gateway.set_running('a',request='原任务')
            obs = await receive(flow,text='请稍后处理另一个问题',private=True)
            await drain(flow, settle=True)
            assert (await flow.store.status_details(obs.id))['status']=='waiting'
            calls = flow.judge.call_count
            flow.clock.advance(5)
            await flow.scheduler.check_due()
            await drain(flow, settle=True)
            assert flow.judge.call_count==calls
            await flow.store.close()
            await flow.store.start()
            await flow.batches.reset_interrupted()
            flow.gateway.finish('a')
            flow.clock.advance(5)
            await flow.scheduler.check_due()
            await drain(flow, settle=True)
            assert len(flow.gateway.submissions)==1
            assert (await flow.store.status_details(obs.id))['status']=='submitted'
        finally:
            await flow.store.close()
    asyncio.run(exercise())


def test_waiting_request_already_resolved_does_not_resubmit(tmp_path):
    async def exercise():
        flow=await make_flow(tmp_path)
        try:
            flow.gateway.set_running('a',request='原任务')
            obs=await receive(flow,text='也问一下当前任务里的事情',private=True)
            await drain(flow, settle=True)
            flow.gateway.finish('a')
            flow.judge.set_choices(intervene='no')
            flow.clock.advance(5)
            await flow.scheduler.check_due()
            await drain(flow, settle=True)
            assert (await flow.store.status_details(obs.id))['status']=='finished'
            assert flow.gateway.submissions==[]
        finally:
            await flow.store.close()
    asyncio.run(exercise())


def test_later_cancellation_rechecks_head_but_stays_separate_payload(tmp_path):
    async def exercise():
        flow=await make_flow(tmp_path)
        try:
            flow.gateway.set_running('a',request='原任务')
            first=await receive(flow,text='待处理请求',private=True)
            await drain(flow, settle=True)
            flow.judge.set_choices(intervene='no')
            cancellation=await receive(flow,text='不要做了',private=True)
            await drain(flow, settle=True)
            assert (await flow.store.status_details(first.id))['status']=='finished'
            await flow.scheduler.replenish()
            await drain(flow, settle=True)
            assert flow.gateway.submissions==[]
            assert (await flow.store.status_details(cancellation.id))['status']=='finished'
            state=flow.judge.calls[-2][0]
            assert '待处理请求' in state['current_pending']
            assert '不要做了' in state['following_background']
        finally:
            await flow.store.close()
    asyncio.run(exercise())
