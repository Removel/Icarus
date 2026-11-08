"""Bounded task-lifetime availability notices, never message-body injection or steering."""
import asyncio
import os

import httpx

from agent_app.contracts.prompt import PromptSection,PromptUpdateEvent
from icarus.core.run_control import TaskChannelStatus
from .inbox import available

NOTICE='当前事项有新的待读补充。可在合适步骤通过 argus_receive 读取完整窗口；通知本身不代表已读或已处理。'


async def watch(plugin,task_id,binding,signal):
    watermark=None
    backoff=1.0
    async with httpx.AsyncClient(timeout=10,trust_env=False,follow_redirects=False,
            headers={'Authorization':'Bearer '+os.getenv('ARGUS_AGENT_API_TOKEN','')}) as client:
        while not binding.get('finished'):
            channel=plugin.task_channels.get(task_id)
            if channel is None or channel.status in {TaskChannelStatus.COMPLETED,TaskChannelStatus.FAILED,TaskChannelStatus.CANCELLED,TaskChannelStatus.CANCELLING}:
                return
            if channel.status != TaskChannelStatus.RUNNING:
                await asyncio.sleep(.01)
                continue
            signal.clear()
            try:
                state=await available(client,binding)
                channel=plugin.task_channels.get(task_id)
                if binding.get('finished') or channel is None or channel.status != TaskChannelStatus.RUNNING:
                    return
                if not state['pending_count']:
                    watermark=None
                elif state.get('watermark') != watermark:
                    await plugin.publish(PromptUpdateEvent(task_id=task_id,sections=(
                        PromptSection('available','runtime',NOTICE),)))
                    watermark=state.get('watermark')
                backoff=30.0
            except (httpx.HTTPError,ValueError):
                backoff=min(max(1.0,backoff*2),30.0)
            try:
                await asyncio.wait_for(signal.wait(),timeout=backoff)
            except TimeoutError:
                pass
