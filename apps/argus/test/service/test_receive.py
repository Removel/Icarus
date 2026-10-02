import asyncio
from apps.argus.test.application.conversation_fakes import make_flow
from apps.argus.src.handler.qq_handler import QQHandler


def test_receive_waits_for_commit_without_remote_work(tmp_path):
    async def exercise():
        flow = await make_flow(tmp_path)
        entered, release = asyncio.Event(), asyncio.Event()
        original = flow.service.store.insert
        async def blocked(row, **kwargs):
            entered.set()
            await release.wait()
            return await original(row, **kwargs)
        flow.service.store.insert = blocked
        obs = QQHandler('10000').parse(dict(post_type='message',self_id=10000,time=1,
            message_id=1,message_type='private',user_id=7,message='hello'))
        task = asyncio.create_task(flow.service.receive(obs))
        try:
            await entered.wait()
            assert not task.done()
            release.set()
            assert await task
            assert flow.judge.call_count == 0 and flow.gateway.submissions == []
            assert (await flow.service.store.get(obs.id)).source_message_id == '1'
        finally:
            release.set()
            if not task.done():
                task.cancel()
                await asyncio.gather(task,return_exceptions=True)
            await flow.store.close()
    asyncio.run(exercise())
