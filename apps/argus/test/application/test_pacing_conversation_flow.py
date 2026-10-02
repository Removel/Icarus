import asyncio
from apps.argus.test.application.conversation_fakes import make_flow, receive, drain


def test_followup_after_completed_answer_may_start_again(tmp_path):
    async def exercise():
        flow=await make_flow(tmp_path)
        try:
            first=await receive(flow,text='第一个问题',private=True)
            await drain(flow, settle=True)
            flow.gateway.finish('new-1')
            followup=await receive(flow,text='那么第二个问题呢',private=True)
            await drain(flow, settle=True)
            assert len(flow.gateway.submissions)==2
            assert '第一个问题' not in flow.gateway.submissions[-1]['prompt']
            assert '第二个问题' in flow.gateway.submissions[-1]['prompt']
            assert (await flow.store.status_details(followup.id))['status']=='submitted'
        finally:
            await flow.store.close()
    asyncio.run(exercise())


def test_new_message_during_judgment_prevents_stale_delivery(tmp_path):
    async def exercise():
        flow=await make_flow(tmp_path)
        try:
            entered,release=asyncio.Event(),asyncio.Event()
            original=flow.judge.ask
            async def paused(**kwargs):
                entered.set()
                await release.wait()
                return await original(**kwargs)
            flow.judge.ask=paused
            obs=await receive(flow,text='普通群聊问题')
            flow.clock.advance(6)
            await flow.scheduler.check_due()
            process=asyncio.create_task(drain(flow, settle=True))
            await entered.wait()
            await receive(flow,text='先不要做',at=True)
            release.set()
            await process
            record=await flow.batches.record((await flow.store.status_details(obs.id))['batch_id'])
            assert record['status']=='waiting'
            assert record['judged_through_seq']==1
            assert flow.gateway.submissions==[]
            flow.judge.ask=original
            flow.judge.set_choices(intervene='no')
            flow.clock.advance(5)
            await flow.scheduler.check_due()
            await drain(flow, settle=True)
            assert (await flow.store.status_details(obs.id))['status']=='finished'
        finally:
            await flow.store.close()
    asyncio.run(exercise())


def test_self_only_prefix_does_not_block_human_and_never_wakes_model(tmp_path):
    async def exercise():
        flow=await make_flow(tmp_path)
        try:
            for _ in range(25):
                await receive(flow,text='自身消息',sender_id='10000')
            await drain(flow, settle=True)
            assert flow.judge.call_count==0 and flow.gateway.submissions==[]
            obs=await receive(flow,text='人类问题',at=True)
            for _ in range(4):
                await flow.scheduler.check_due()
                await drain(flow, settle=True)
            assert (await flow.store.status_details(obs.id))['status']=='submitted'
            assert len(flow.gateway.submissions)==1
        finally:
            await flow.store.close()
    asyncio.run(exercise())
