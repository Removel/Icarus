import asyncio
from apps.argus.test.application.conversation_fakes import make_flow, receive
from apps.argus.src.service.batch_runtime import BatchRuntime


def test_background_model_does_not_block_receiving_another_message(tmp_path):
    async def exercise():
        flow = await make_flow(tmp_path)
        entered, release = asyncio.Event(), asyncio.Event()
        original = flow.judge.ask
        async def blocked(**kwargs):
            entered.set()
            await release.wait()
            return await original(**kwargs)
        flow.judge.ask = blocked
        flow.judge.set_choices(intervene='no')
        await receive(flow,text='group question')
        flow.clock.advance(6)
        await flow.scheduler.check_due()
        runtime = BatchRuntime(flow.scheduler,flow.service,recovery_interval_seconds=1)
        task = asyncio.create_task(runtime.run_batches())
        try:
            await entered.wait()
            from apps.argus.src.handler.qq_handler import QQHandler
            obs = QQHandler('10000').parse(dict(post_type='message',self_id=10000,time=1,
                message_id=100,message_type='private',user_id=8,message='new'))
            async with asyncio.timeout(1):
                assert await flow.service.receive(obs)
            assert (await flow.service.store.get(obs.id)).source_message_id == '100'
            assert not task.done()
        finally:
            release.set()
            task.cancel()
            await asyncio.gather(task,return_exceptions=True)
            await flow.store.close()
    asyncio.run(exercise())


def test_checks_wakes_on_notify_and_is_cancellable(tmp_path):
    async def exercise():
        flow = await make_flow(tmp_path)
        runtime = BatchRuntime(flow.scheduler,flow.service,recovery_interval_seconds=60)
        checked = asyncio.Event()
        original = flow.scheduler.check_due
        async def check_due():
            await original()
            checked.set()
        flow.scheduler.check_due = check_due
        task = asyncio.create_task(runtime.run_checks())
        try:
            await checked.wait()
            checked.clear()
            flow.scheduler.notify(('qq','10000','qq:private:7'),human=True)
            async with asyncio.timeout(1):
                await checked.wait()
        finally:
            task.cancel()
            await asyncio.gather(task,return_exceptions=True)
            assert task.cancelled()
            await flow.store.close()
    asyncio.run(exercise())
