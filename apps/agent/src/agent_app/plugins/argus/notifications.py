"""A task-scoped availability recheck worker; Argus remains the durable inbox."""

import asyncio

from agent_app.argus_protocol import HandoffError
from agent_app.contracts.prompt import PromptSection, PromptUpdateEvent
from icarus.core.run_control import TaskChannelStatus

AVAILABILITY_NOTICE = '当前语境有新的待读消息，可在合适时通过工具面 {scope} 读取（先 tool_search，再 tool_execute）。'


def availability_notice(plugin_id: str) -> str:
    return AVAILABILITY_NOTICE.format(scope=plugin_id)


async def watch_availability(plugin, task_id):
    signal = plugin.availability_signal
    backoff = 0.1
    while task_id == plugin.task_context.metadata.get('task_id') and not plugin._task_finished:
        if not plugin._context_ready:
            signal.clear()
            if not plugin._context_ready:
                await signal.wait()
            continue
        # Register first, then query. A signal set during a query is retained for
        # the next pass; absence of a signal never owns or acknowledges messages.
        signal.clear()
        try:
            state = await plugin.client.call(plugin.task_context.metadata['wake_id'], 'available',
                                             {'binding_id': plugin.binding['binding_id']})
            count, watermark = state.get('pending_count'), state.get('watermark')
            if type(count) is not int or count < 0 or not isinstance(watermark, (str, type(None))):
                raise HandoffError('protocol_error')
            if not count:
                plugin._notice_sent = False
            elif not plugin._notice_sent and plugin._context_ready:
                channel = plugin.task_channels.get(task_id) if plugin.task_channels is not None else None
                if channel is None or channel.status is TaskChannelStatus.RUNNING:
                    await plugin.publish(PromptUpdateEvent(task_id=task_id, sections=(
                        PromptSection('available', 'runtime', availability_notice(plugin.plugin_id)),)))
                    plugin._notice_sent = True
                else:
                    # The same ContextReady fans out to both consumers. Wait for
                    # Agent's existing task channel to open before one fixed notice.
                    await asyncio.sleep(0.01)
                    continue
            backoff = 0.1
            try:
                await asyncio.wait_for(signal.wait(), timeout=30)
            except TimeoutError:
                pass  # Reconcile a lost signal without renewing semantic activity.
        except HandoffError:
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30)
