"""Initial prompt delivery and final-budget tool output confirmation."""

import asyncio

from agent_app.contracts.prompt import PromptContributionEvent
from icarus.core.agents import AgentToolCompletedEvent
from icarus.core.contracts.input import UserInputEvent
from icarus.core.models.messages import ToolCall
from icarus.core.session import SessionContext
from icarus.core.tools import ToolExecutionResult


ITEM = '60000000-0000-4000-8000-000000000001'
NEW = '60000000-0000-4000-8000-000000000002'
TASK = '50000000-0000-4000-8000-000000000001'


def test_argus_plugin_delivers_initial_and_only_confirms_unmodified_final_tool_result():
    async def run():
        from agent_app.plugins.argus.plugin import ArgusPlugin

        calls, records, events = [], [], []
        item = {'observation_id': ITEM, 'role': 'work', 'origin': 'initial', 'excerpt': 'question'}
        next_item = {'observation_id': NEW, 'role': 'work', 'origin': 'incidental', 'excerpt': 'reply'}
        binding = {'binding_id': 'binding', 'initial_items': [item], 'initial_cursor': 'initial'}
        context = SessionContext(kind='argus_background', scope={'source': 'qq'}, metadata={
            'wake_id': 'wake', 'task_id': TASK, 'binding': binding,
        })

        class Client:
            async def call(self, wake, operation, payload):
                calls.append((operation, payload))
                if operation == 'wait':
                    return {'items': [next_item], 'next_cursor': 'next', 'has_more': False, 'truncated': False}
                return {'delivered_ids': [ITEM]}
            async def aclose(self): pass

        async def append(**kwargs):
            records.append(kwargs)
            return len(records)

        plugin = ArgusPlugin('argus', context=context, client=Client(), append_record=append)
        plugin.bind_publisher(lambda event: collect(events, event))
        await plugin.on_input('user-input', UserInputEvent(task_id=TASK, prompt='work'))
        assert isinstance(events[0], PromptContributionEvent)
        assert ITEM in plugin.exposed
        assert 'question' in events[0].sections[0].content
        assert 'question' not in plugin.static_prompt_sections()[0].content
        result = await plugin.execute('wait', {}, task_id=TASK, tool_call_id='call')
        assert NEW not in plugin.exposed
        await plugin.on_tool_completed('agent', AgentToolCompletedEvent(
            task_id=TASK, step=1, tool_call=ToolCall('call', 'argus_wait', {}),
            result=ToolExecutionResult(True, output='truncated preview'),
        ))
        assert NEW not in plugin.exposed and not records
        await plugin.on_tool_completed('agent', AgentToolCompletedEvent(
            task_id=TASK, step=1, tool_call=ToolCall('call', 'argus_wait', {}), result=result,
        ))
        assert NEW in plugin.exposed
        assert records[-1]['update_type'] == 'argus_wait_cursor'
        assert records[-1]['payload']['last_returned_cursor'] == 'next'

    asyncio.run(run())


def test_report_retry_reuses_pending_identity_and_does_not_resend_after_success():
    async def run():
        from agent_app.plugins.argus.plugin import ArgusPlugin
        calls, records = [], []
        context = SessionContext(kind='argus_background', metadata={'wake_id': 'wake', 'task_id': TASK,
            'binding': {'binding_id': 'binding', 'initial_cursor': 'cursor'}})
        class Client:
            async def call(self, wake, operation, payload):
                calls.append(payload['report_id'])
                return {'report_id': payload['report_id'], 'disposition': 'accepted', 'report_cursor': 1}
        async def append(**values): records.append(values); return len(records)
        plugin = ArgusPlugin('argus', context=context, client=Client(), append_record=append)
        plugin.exposed.add(ITEM)
        plugin.roles[ITEM] = 'work'
        arguments = {'items': [{'observation_id': ITEM, 'status': 'handled'}]}
        assert (await plugin.execute('report', arguments, task_id=TASK)).success
        assert (await plugin.execute('report', arguments, task_id=TASK)).success
        assert len(calls) == 1
        assert len([row for row in records if row['update_type'] == 'argus_report_synced']) == 1
    asyncio.run(run())


async def collect(events, event):
    events.append(event)


def test_pending_earlier_report_is_retried_before_later_report():
    async def run():
        from agent_app.plugins.argus.plugin import ArgusPlugin
        from agent_app.argus_protocol import HandoffError
        records, requests = [], []
        context = SessionContext(kind='argus_background', metadata={'wake_id': 'wake', 'task_id': TASK,
            'binding': {'binding_id': 'binding'}})
        class Client:
            async def call(self, wake, operation, payload):
                requests.append(payload)
                if len(requests) == 1: raise HandoffError('unavailable', retryable=True)
                return {'report_id': payload['report_id'], 'disposition': 'accepted', 'report_cursor': len(requests)-1}
        async def append(**values): records.append(values); return len(records)
        plugin = ArgusPlugin('argus', context=context, client=Client(), append_record=append)
        plugin.exposed.update({ITEM, NEW})
        plugin.roles.update({ITEM:'work', NEW:'work'})
        first = await plugin.execute('report', {'items':[{'observation_id':ITEM,'status':'handled'}]}, task_id=TASK)
        assert not first.success
        second = await plugin.execute('report', {'items':[{'observation_id':NEW,'status':'handled'}]}, task_id=TASK)
        assert second.success
        assert [value['items'][0]['observation_id'] for value in requests] == [ITEM, ITEM, NEW]
        assert requests[0]['report_id'] == requests[1]['report_id']
    asyncio.run(run())


def test_duplicate_report_items_are_rejected_before_outbox_write():
    async def run():
        from agent_app.plugins.argus.plugin import ArgusPlugin
        records = []
        context = SessionContext(kind='argus_background', metadata={'wake_id':'wake', 'task_id':TASK,
            'binding':{'binding_id':'binding'}})
        async def append(**value): records.append(value)
        plugin = ArgusPlugin('argus', context=context, append_record=append)
        plugin.exposed.add(ITEM)
        plugin.roles[ITEM] = 'work'
        result = await plugin.execute('report', {'items':[{'observation_id':ITEM,'status':'handled'}]*2}, task_id=TASK)
        assert not result.success and result.error == 'invalid_report'
        assert records == []
    asyncio.run(run())
