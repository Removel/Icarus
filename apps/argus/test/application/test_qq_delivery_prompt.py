import asyncio
from apps.argus.test.application.conversation_fakes import make_flow, receive, drain


def test_submit_includes_unhandled_context_but_not_previous_delivered_request(tmp_path):
    async def exercise():
        flow = await make_flow(tmp_path)
        try:
            flow.judge.set_choices(intervene='no')
            await receive(flow, text='我们缺乏美术素材')
            flow.clock.advance(1)
            await flow.scheduler.check_due()
            await drain(flow, settle=True)
            flow.judge.set_choices(intervene='yes')
            await receive(flow, text='你能帮忙制作吗', at=True)
            await drain(flow, settle=True)
            prompt = flow.gateway.submissions[-1]['prompt']
            assert '我们缺乏美术素材' in prompt
            assert '【仅供理解的上文，不是新的执行请求】' in prompt
            assert '【本次需要处理的消息】' in prompt
            assert prompt.index('我们缺乏美术素材') < prompt.index('【本次需要处理的消息】')
            assert '新启动' in prompt
            assert '不要再向 TUI 汇报' in prompt
            flow.gateway.finish('new-1')
            await receive(flow, text='继续聊第二个问题', at=True)
            await drain(flow, settle=True)
            followup = flow.gateway.submissions[-1]['prompt']
            assert '你能帮忙制作吗' not in followup
            assert '已有会话历史' in followup
        finally:
            await flow.store.close()
    asyncio.run(exercise())


def test_steer_prompt_is_an_addition_not_a_second_independent_request(tmp_path):
    async def exercise():
        flow = await make_flow(tmp_path)
        try:
            flow.gateway.set_running('existing', request='正在完成的任务')
            flow.judge.set_choices(relation='steer')
            await receive(flow, text='补充：背景用蓝色', private=True)
            await drain(flow, settle=True)
            prompt = flow.gateway.steers[-1]['prompt']
            assert '补充当前任务' in prompt
            assert 'existing' in prompt
            assert '不要从头重复执行' in prompt
            assert '补充：背景用蓝色' in prompt
            assert '正在完成的任务' not in prompt
            assert 'send_private_msg' in prompt
        finally:
            await flow.store.close()
    asyncio.run(exercise())
